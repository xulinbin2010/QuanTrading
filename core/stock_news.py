"""
个股深度信息：SEC 公告解析 + 分析师数据 + 新闻。

数据源：
  1. SEC EDGAR（8-K 解析）
       - CIK 映射：sec.gov/files/company_tickers.json（7 天缓存）
       - 近期申报列表：data.sec.gov/submissions/CIK{cik}.json
       - 8-K 内容解析：提取 Item 编号并映射为中文事件类型 + 正文摘要
  2. yfinance 分析师数据
       - 目标价（均值/高/低）、评级分布、近期评级变动
       - 下次财报日期 + EPS/营收预期
       - 最近 5 季营收趋势
  3. yfinance 新闻（辅助）

缓存：
  .sec_cik_cache.pkl   — CIK 映射，7 天 TTL
  .stock_news_cache.pkl — 每 symbol 独立 entry，2 小时 TTL
"""
from __future__ import annotations

import logging
import pickle
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

_logger = logging.getLogger(__name__)

_CIK_CACHE_FILE  = Path('.sec_cik_cache.pkl')
_NEWS_CACHE_FILE = Path('.stock_news_cache.pkl')
_CIK_TTL         = timedelta(days=7)
_NEWS_TTL         = timedelta(hours=2)

_SEC_HEADERS = {'User-Agent': 'QuanTrading xulinbin1988@gmail.com'}

_FILING_FORMS = {'8-K', '10-K', '10-Q', '10-K/A', '10-Q/A'}

# 8-K Item 编号 → 中文事件类型（投资者最关注的）
_8K_ITEMS: dict[str, str] = {
    '1.01': '重大协议签署',
    '1.02': '重大协议终止',
    '1.03': '破产/接管',
    '2.01': '并购完成',
    '2.02': '财报发布',
    '2.03': '新增重大债务',
    '2.05': '重组/裁员费用',
    '2.06': '资产减值',
    '3.01': '退市警告',
    '3.02': '非公开增发',
    '4.01': '审计师变更',
    '5.01': '控制权变更',
    '5.02': '高管变动',
    '5.03': '章程修改',
    '5.07': '股东投票',
    '7.01': 'Reg FD 披露',
    '8.01': '其他重大事项',
    '9.01': '附件',
}

# 对投资者有重要意义的 Item（高优先级）
_HIGH_PRIORITY_ITEMS = {'2.02', '2.01', '5.02', '1.01', '3.01', '2.06', '1.02', '5.01'}


# ── HTML 纯文本提取 ──────────────────────────────────────────

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            s = data.strip()
            if s:
                self._parts.append(s)

    def get_text(self) -> str:
        return ' '.join(self._parts)


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.get_text()


# ── CIK 映射 ────────────────────────────────────────────────

def _load_cik_map() -> dict[str, int]:
    if _CIK_CACHE_FILE.exists():
        try:
            stored = pickle.loads(_CIK_CACHE_FILE.read_bytes())
            if datetime.now() - stored['_time'] < _CIK_TTL:
                return stored['data']
        except Exception:
            pass
    try:
        resp = requests.get(
            'https://www.sec.gov/files/company_tickers.json',
            headers=_SEC_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        cik_map = {v['ticker'].upper(): v['cik_str'] for v in raw.values()}
        _CIK_CACHE_FILE.write_bytes(pickle.dumps({'_time': datetime.now(), 'data': cik_map}))
        return cik_map
    except Exception as e:
        _logger.warning(f'[SEC] CIK 映射下载失败：{e}')
        return {}


def _get_cik(symbol: str) -> int | None:
    return _load_cik_map().get(symbol.upper())


# ── 8-K 内容解析 ────────────────────────────────────────────

def _parse_8k_content(url: str) -> dict:
    """
    下载 8-K HTML，提取 Item 编号列表和正文摘要。
    返回 {items: [(code, label)], snippet: str, priority: bool}
    """
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=12)
        resp.raise_for_status()
        text = _html_to_text(resp.text)
    except Exception:
        return {'items': [], 'snippet': '', 'priority': False}

    # 提取所有 Item 编号，格式：Item 2.02 / ITEM 2.02 / Item 2.02
    found_codes = re.findall(r'[Ii]tem\s+(\d+\.\d+)', text)
    # 去重保持顺序，过滤掉 9.01（附件，无信息价值）
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for code in found_codes:
        if code not in seen and code != '9.01' and code in _8K_ITEMS:
            seen.add(code)
            items.append((code, _8K_ITEMS[code]))

    priority = any(code in _HIGH_PRIORITY_ITEMS for code, _ in items)

    # 正文摘要：跳过头部套话，取第一段实质性内容（300 字）
    # 找到第一个 Item 出现后的文字
    item_match = re.search(r'[Ii]tem\s+\d+\.\d+', text)
    start = item_match.end() if item_match else 0
    raw_snippet = text[start:start + 800].strip()
    # 清理多余空格
    raw_snippet = re.sub(r'\s+', ' ', raw_snippet)[:300]

    return {'items': items, 'snippet': raw_snippet, 'priority': priority}


# ── SEC 近期申报（含 8-K 解析）───────────────────────────────

def _fetch_sec_filings(symbol: str) -> list[dict]:
    cik = _get_cik(symbol)
    if cik is None:
        return []

    cik_str = str(cik).zfill(10)
    url = f'https://data.sec.gov/submissions/CIK{cik_str}.json'
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _logger.warning(f'[SEC] {symbol} 申报数据获取失败：{e}')
        return []

    recent     = data.get('filings', {}).get('recent', {})
    forms      = recent.get('form', [])
    dates      = recent.get('filingDate', [])
    accessions = recent.get('accessionNumber', [])
    docs       = recent.get('primaryDocument', [])

    results: list[dict] = []
    parsed_8k = 0  # 限制解析 8-K 内容的数量，避免太多 HTTP 请求

    for form, date, acc, doc in zip(forms, dates, accessions, docs):
        if form not in _FILING_FORMS:
            continue
        acc_nodash = acc.replace('-', '')
        filing_url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}'

        entry: dict = {
            'date':     date,
            'form':     form,
            'url':      filing_url,
            'items':    [],
            'snippet':  '',
            'priority': False,
            'label':    '',   # 前端显示用的主标签
        }

        if form in ('8-K', '8-K/A') and parsed_8k < 5:
            content = _parse_8k_content(filing_url)
            entry['items']    = content['items']
            entry['snippet']  = content['snippet']
            entry['priority'] = content['priority']
            # 主标签：用最重要的 Item，没有就用表单类型
            if content['items']:
                # 优先显示高优先级 Item
                hi = [label for code, label in content['items'] if code in _HIGH_PRIORITY_ITEMS]
                entry['label'] = hi[0] if hi else content['items'][0][1]
            else:
                entry['label'] = '重大事项公告'
            parsed_8k += 1
        elif form == '10-K':
            entry['label'] = '年度报告 (10-K)'
        elif form == '10-K/A':
            entry['label'] = '年度报告修正 (10-K/A)'
        elif form == '10-Q':
            entry['label'] = '季度报告 (10-Q)'
        elif form == '10-Q/A':
            entry['label'] = '季度报告修正 (10-Q/A)'
        else:
            entry['label'] = form

        results.append(entry)
        if len(results) >= 10:
            break

    return results


# ── yfinance 分析师数据 ──────────────────────────────────────

def _fetch_analyst_data(symbol: str) -> dict:
    """
    返回：
      target_price: {current, mean, high, low}
      recommendation: {strongBuy, buy, hold, sell, strongSell, key}
      recent_changes: [{date, firm, action, to_grade, from_grade, target}]
      next_earnings: {date, eps_avg, eps_high, eps_low, rev_avg_b}
      quarterly_revenue: [{quarter, revenue_b}]   最近 5 季
    """
    result: dict = {
        'target_price':      None,
        'recommendation':    None,
        'recent_changes':    [],
        'next_earnings':     None,
        'quarterly_revenue': [],
    }
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)

        # 目标价
        try:
            tp = t.analyst_price_targets
            if tp and isinstance(tp, dict):
                result['target_price'] = {
                    'current': tp.get('current'),
                    'mean':    round(tp.get('mean', 0), 2),
                    'high':    tp.get('high'),
                    'low':     tp.get('low'),
                }
        except Exception:
            pass

        # 评级分布（最新月）
        try:
            rs = t.recommendations_summary
            if rs is not None and not rs.empty:
                row = rs.iloc[0]
                result['recommendation'] = {
                    'strongBuy':  int(row.get('strongBuy', 0)),
                    'buy':        int(row.get('buy', 0)),
                    'hold':       int(row.get('hold', 0)),
                    'sell':       int(row.get('sell', 0)),
                    'strongSell': int(row.get('strongSell', 0)),
                }
        except Exception:
            pass

        # 近期评级变动（最近 6 条，过滤掉纯维持）
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                ud = ud.reset_index()
                changes = []
                for _, row in ud.head(20).iterrows():
                    action = str(row.get('Action', '')).lower()
                    if action in ('reit',):   # 跳过纯维持
                        continue
                    dt = row.get('GradeDate') or row.get('Date', '')
                    date_str = str(dt)[:10] if dt else ''
                    changes.append({
                        'date':       date_str,
                        'firm':       str(row.get('Firm', '')),
                        'action':     action,   # main=升/降, init=首次, reit=维持
                        'to_grade':   str(row.get('ToGrade', '')),
                        'from_grade': str(row.get('FromGrade', '')),
                        'target':     float(row['currentPriceTarget']) if row.get('currentPriceTarget') else None,
                    })
                    if len(changes) >= 6:
                        break
                result['recent_changes'] = changes
        except Exception:
            pass

        # 下次财报
        try:
            cal = t.calendar
            if cal and isinstance(cal, dict):
                ed = cal.get('Earnings Date')
                if ed:
                    if isinstance(ed, list):
                        ed = ed[0]
                    result['next_earnings'] = {
                        'date':      str(ed),
                        'eps_avg':   cal.get('Earnings Average'),
                        'eps_high':  cal.get('Earnings High'),
                        'eps_low':   cal.get('Earnings Low'),
                        'rev_avg_b': round(cal['Revenue Average'] / 1e9, 2)
                                     if cal.get('Revenue Average') else None,
                    }
        except Exception:
            pass

        # 季度营收（最近 5 季）
        try:
            fin = t.quarterly_financials
            if fin is not None and 'Total Revenue' in fin.index:
                rev_series = fin.loc['Total Revenue'].dropna().head(5)
                result['quarterly_revenue'] = [
                    {
                        'quarter':   str(dt)[:7],   # 2025-10
                        'revenue_b': round(float(v) / 1e9, 2),
                    }
                    for dt, v in rev_series.items()
                ]
        except Exception:
            pass

    except Exception as e:
        _logger.warning(f'[Analyst] {symbol} 分析师数据获取失败：{e}')

    return result


# ── yfinance 新闻 ────────────────────────────────────────────

def _fetch_yf_news(symbol: str) -> list[dict]:
    try:
        import yfinance as yf
        raw_news = yf.Ticker(symbol).news or []
    except Exception as e:
        _logger.warning(f'[News] {symbol} yfinance 新闻获取失败：{e}')
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
    results = []
    for item in raw_news:
        content = item.get('content') or item
        title = content.get('title', '')
        if not title:
            continue
        pub_date_raw = content.get('pubDate') or content.get('displayTime')
        pub_dt = None
        if isinstance(pub_date_raw, str):
            try:
                pub_dt = datetime.fromisoformat(pub_date_raw.replace('Z', '+00:00'))
            except Exception:
                pass
        elif isinstance(pub_date_raw, (int, float)):
            pub_dt = datetime.fromtimestamp(pub_date_raw, tz=timezone.utc)
        if pub_dt and pub_dt < cutoff:
            continue
        canonical  = content.get('canonicalUrl') or {}
        click_url  = content.get('clickThroughUrl') or {}
        url        = (canonical.get('url') or click_url.get('url')
                      or content.get('link') or content.get('url', ''))
        provider   = content.get('provider') or {}
        publisher  = provider.get('displayName') or content.get('publisher', '')
        results.append({
            'date':      pub_dt.strftime('%Y-%m-%d') if pub_dt else '',
            'title':     title,
            'url':       url,
            'publisher': publisher,
        })
        if len(results) >= 6:
            break
    return results


# ── 主入口 ───────────────────────────────────────────────────

def get_stock_news(symbol: str) -> dict:
    """
    返回 {symbol, sec_filings, analyst, news, cached_at}。
    任一源失败只记录警告，其余正常返回。
    """
    symbol = symbol.upper()

    cache: dict = {}
    if _NEWS_CACHE_FILE.exists():
        try:
            cache = pickle.loads(_NEWS_CACHE_FILE.read_bytes())
        except Exception:
            cache = {}

    entry = cache.get(symbol, {})
    if entry and datetime.now() - entry['ts'] < _NEWS_TTL:
        return entry['data']

    sec_filings = _fetch_sec_filings(symbol)
    analyst     = _fetch_analyst_data(symbol)
    news        = _fetch_yf_news(symbol)

    result = {
        'symbol':      symbol,
        'sec_filings': sec_filings,
        'analyst':     analyst,
        'news':        news,
        'cached_at':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    cache[symbol] = {'ts': datetime.now(), 'data': result}
    try:
        _NEWS_CACHE_FILE.write_bytes(pickle.dumps(cache))
    except Exception as e:
        _logger.warning(f'[News] 缓存写入失败：{e}')

    return result


def get_news_light_batch(symbols: list[str], max_workers: int = 8) -> dict[str, list[dict]]:
    """
    批量轻量新闻：仅 yfinance 标题（无 SEC/分析师，适合大股票池扫描）。
    线程池并发拉取，缓存读一次、写一次（key `SYM#light`，TTL 同 _NEWS_TTL），
    避免逐票写 pickle 的并发损坏。full 缓存（get_stock_news）命中时直接复用其 news。
    """
    from concurrent.futures import ThreadPoolExecutor

    symbols = [s.upper() for s in symbols]
    cache: dict = {}
    if _NEWS_CACHE_FILE.exists():
        try:
            cache = pickle.loads(_NEWS_CACHE_FILE.read_bytes())
        except Exception:
            cache = {}

    now = datetime.now()
    out: dict[str, list[dict]] = {}
    todo: list[str] = []
    for s in symbols:
        full = cache.get(s, {})
        if full and now - full['ts'] < _NEWS_TTL:          # full 缓存直接复用
            out[s] = full['data'].get('news') or []
            continue
        light = cache.get(f'{s}#light', {})
        if light and now - light['ts'] < _NEWS_TTL:
            out[s] = light['data'] or []
            continue
        todo.append(s)

    if todo:
        def _safe_fetch(sym: str) -> list[dict]:
            try:
                return _fetch_yf_news(sym)
            except Exception as e:
                _logger.warning(f'[News] {sym} 轻量新闻失败：{e}')
                return []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for sym, news in zip(todo, ex.map(_safe_fetch, todo)):
                out[sym] = news
                cache[f'{sym}#light'] = {'ts': datetime.now(), 'data': news}
        try:
            _NEWS_CACHE_FILE.write_bytes(pickle.dumps(cache))
        except Exception as e:
            _logger.warning(f'[News] 缓存写入失败：{e}')

    return out
