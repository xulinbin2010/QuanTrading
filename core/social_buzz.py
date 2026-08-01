"""社区热度采集（L1 结构化层）：ApeWisdom / Reddit 热帖 / StockTwits 情绪。

设计要点：
- 只采数字与标题样本，不做任何 LLM 调用（叙事解释在 intel_svc 按需触发，L2）
- 监控集（AI 池 + 持仓）由调用方传入 —— core 层不 import web 层
- 三源相互独立：单源失败只丢该源数据，不影响其余（免费 API 无 SLA，可插拔）
- 网络：requests 默认 trust_env=True 走系统代理（Reddit/StockTwits 需代理可达）；
  **不改全局 no_proxy / os.environ**，与 akshare 国内直连（A 股）共存
- ticker 识别防误报：优先认 $TICKER；裸词只认监控白名单内的全大写 token，
  易撞普通英文单词的代码（ARM/WOLF 等）必须带 $ 前缀才计数
"""
from __future__ import annotations

import re
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

_logger = logging.getLogger(__name__)
_ET = ZoneInfo('America/New_York')

_UA = {'User-Agent': 'Mozilla/5.0 (personal research; QuanTrading)'}
_TIMEOUT = 15
_APE_RETRIES = 2

# 这些代码同时是常见英文单词，裸词匹配误报率高 → 只认 $TICKER 形式
_AMBIGUOUS = {'ARM', 'WOLF', 'FLEX', 'CARS', 'GOLD', 'REAL', 'OPEN', 'RUN',
              'ALL', 'ON', 'IT', 'ANY', 'NOW', 'PLAY', 'BIG', 'SEE', 'CEO'}

# Reddit 热帖抓取板块（覆盖散户大盘情绪 + 半导体垂直讨论）
REDDIT_SUBS = ('wallstreetbets', 'stocks', 'Semiconductors', 'hardware')


def et_trade_date() -> str:
    """美东日期（社区热度按美股交易日聚合）。"""
    return datetime.now(_ET).strftime('%Y-%m-%d')


# ── 源 1：ApeWisdom（Reddit 全站 ticker 提及聚合，现成排行榜）─────────────

def _batch(source: str, rows: list[dict], status: str, requested: int,
           covered: int, detail: str = '') -> dict:
    return {
        'source': source,
        'rows': rows,
        'status': status,
        'requested_count': requested,
        'covered_count': covered,
        'row_count': len(rows),
        'detail': detail,
    }


def _get_apewisdom_page(page: int):
    """ApeWisdom 的网络 timeout 是瞬时故障，有限重试后仍失败才降级。"""
    last_error = None
    for attempt in range(_APE_RETRIES + 1):
        try:
            response = requests.get(
                f'https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}',
                headers=_UA, timeout=_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < _APE_RETRIES:
                time.sleep(1.0 * (attempt + 1))
    assert last_error is not None
    raise last_error


def fetch_apewisdom(monitored: set[str], pages: int = 2) -> dict:
    """拉全站提及榜前 pages×100 名，过滤到监控集。

    字段：mentions=24h 提及数，rank=全站排名，upvotes=相关帖子赞数。
    监控票不在榜内 = 热度低于前 200 截断线，服务层按 0 处理。
    """
    rows, td = [], et_trade_date()
    fetched_pages = 0
    last_error = ''
    for page in range(1, pages + 1):
        try:
            r = _get_apewisdom_page(page)
            results = r.json().get('results') or []
        except Exception as e:
            _logger.warning(f'[social] apewisdom page{page} 失败：{e}')
            last_error = str(e)
            break
        fetched_pages += 1
        for it in results:
            sym = str(it.get('ticker') or '').upper()
            if sym not in monitored:
                continue
            def _i(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
            rows.append({
                'symbol': sym, 'source': 'apewisdom', 'trade_date': td,
                'mentions': _i(it.get('mentions')),
                'rank':     _i(it.get('rank')),
                'upvotes':  _i(it.get('upvotes')),
                'extra': {'mentions_24h_ago': _i(it.get('mentions_24h_ago')),
                          'rank_24h_ago':     _i(it.get('rank_24h_ago'))},
            })
    if fetched_pages == pages:
        status = 'ok'
    elif fetched_pages > 0:
        status = 'partial'
    else:
        status = 'unavailable'
    detail = (
        f'排行榜前 {fetched_pages * 100} 名；未命中表示低于榜单截断线'
        if fetched_pages else f'请求失败：{last_error}'
    )
    return _batch('apewisdom', rows, status, len(monitored),
                  len({r['symbol'] for r in rows}), detail)


# ── 源 2：Reddit 热帖（标题级上下文，公开 JSON 端点）──────────────────────

def _match_tickers(text: str, monitored: set[str]) -> set[str]:
    """从帖子标题提取监控集内的 ticker。$X 直接认；裸词要求全大写 token 且不在歧义表。"""
    hits: set[str] = set()
    for m in re.findall(r'\$([A-Za-z]{1,6})\b', text):
        if m.upper() in monitored:
            hits.add(m.upper())
    for tok in re.findall(r'\b[A-Z]{2,6}\b', text):
        if tok in monitored and tok not in _AMBIGUOUS:
            hits.add(tok)
    return hits


def _reddit_token() -> str | None:
    """Reddit OAuth application-only token（免费）。

    匿名 JSON 端点已对数据中心/代理 IP 全面 403，必须走 OAuth：
    在 https://www.reddit.com/prefs/apps 创建 script 类型应用（免费、秒批），
    把 client_id / secret 写入 .env 的 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET。
    未配置返回 None，本源整体跳过（提及数主信号由 ApeWisdom 承担，不受影响）。
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass
    cid = os.environ.get('REDDIT_CLIENT_ID')
    sec = os.environ.get('REDDIT_CLIENT_SECRET')
    if not cid or not sec:
        return None
    try:
        r = requests.post(
            'https://www.reddit.com/api/v1/access_token',
            auth=(cid, sec), data={'grant_type': 'client_credentials'},
            headers=_UA, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json().get('access_token')
    except Exception as e:
        _logger.warning(f'[social] reddit OAuth 失败：{e}')
        return None


def fetch_reddit_posts(monitored: set[str],
                       subs: tuple[str, ...] = REDDIT_SUBS,
                       limit: int = 75) -> dict:
    """扫各板块 hot 帖标题，统计监控票命中帖数/合计赞数，并留最热 3 条标题做展示样本。

    需 .env 配置 REDDIT_CLIENT_ID/SECRET（见 _reddit_token）；未配置时返回空（源级降级）。
    """
    token = _reddit_token()
    if not token:
        _logger.info('[social] 未配置 Reddit OAuth（REDDIT_CLIENT_ID/SECRET），跳过热帖标题源')
        return _batch('reddit_posts', [], 'disabled', len(subs), 0,
                      '未配置 REDDIT_CLIENT_ID/SECRET')
    headers = dict(_UA, Authorization=f'Bearer {token}')
    td = et_trade_date()
    agg: dict[str, dict] = {}
    covered_subs = 0
    errors = []
    for sub in subs:
        try:
            r = requests.get(
                f'https://oauth.reddit.com/r/{sub}/hot',
                params={'limit': limit}, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            children = (r.json().get('data') or {}).get('children') or []
        except Exception as e:
            _logger.warning(f'[social] reddit r/{sub} 失败：{e}')
            errors.append(f'r/{sub}: {e}')
            continue
        covered_subs += 1
        for ch in children:
            d = ch.get('data') or {}
            title = str(d.get('title') or '')
            score = int(d.get('score') or 0)
            for sym in _match_tickers(title, monitored):
                a = agg.setdefault(sym, {'posts': 0, 'score': 0, 'titles': []})
                a['posts'] += 1
                a['score'] += score
                a['titles'].append((score, f'r/{sub}: {title}'))
        time.sleep(0.5)   # 公开端点限速礼貌间隔
    rows = [{
        'symbol': sym, 'source': 'reddit_posts', 'trade_date': td,
        'mentions': a['posts'], 'upvotes': a['score'],
        'extra': {'titles': [t for _, t in sorted(a['titles'], reverse=True)[:3]]},
    } for sym, a in agg.items()]
    status = 'ok' if covered_subs == len(subs) else ('partial' if covered_subs else 'unavailable')
    detail = '; '.join(errors[:2]) if errors else f'覆盖 {covered_subs} 个 subreddit'
    return _batch('reddit_posts', rows, status, len(subs), covered_subs, detail)


# ── 源 3：StockTwits（逐票消息流，自带 Bullish/Bearish 标签）───────────────

def fetch_stocktwits(symbols: list[str],
                     max_symbols: int = 80,
                     pause: float = 0.4) -> dict:
    """逐票拉最近 30 条消息，只统计其中近 24h 的消息与情绪标签。

    未认证配额约 200 请求/小时/IP：单轮 cap 到 max_symbols，命中 429 立即停
    （symbols 按优先级传入——持仓在前，剩余配额给热度高的池内票）。
    """
    rows, td = [], et_trade_date()
    cutoff = datetime.now(_ET) - timedelta(hours=24)
    targets = symbols[:max_symbols]
    covered = 0
    errors = []
    rate_limited = False
    cloudflare_blocked = False
    for sym in targets:
        try:
            r = requests.get(
                f'https://api.stocktwits.com/api/2/streams/symbol/{sym}.json',
                headers=_UA, timeout=_TIMEOUT)
            if r.status_code == 429:
                _logger.warning(f'[social] stocktwits 触发限流（已采 {len(rows)} 只），本轮提前结束')
                rate_limited = True
                break
            # 2026-07 起该匿名 API 在部分网络出口会返回 Cloudflare browser challenge。
            # 这不是 ticker 无数据，也不是可通过重试解决的 403；继续 80 次请求只会制造
            # 噪音并加重封锁，因此立刻熔断本源，前端明确标为 unavailable。
            if r.status_code == 403 and str(r.headers.get('cf-mitigated', '')).lower() == 'challenge':
                _logger.warning('[social] stocktwits 被 Cloudflare browser challenge 拦截，'
                                '本轮停止请求并将该源标为 unavailable')
                cloudflare_blocked = True
                break
            if r.status_code == 404:      # 该票无 stream（新股/冷门）
                covered += 1
                continue
            r.raise_for_status()
            msgs = r.json().get('messages') or []
        except Exception as e:
            _logger.warning(f'[social] stocktwits {sym} 失败：{e}')
            errors.append(f'{sym}: {e}')
            continue
        covered += 1
        bull = bear = recent = 0
        for m in msgs:
            try:
                created = datetime.strptime(m['created_at'], '%Y-%m-%dT%H:%M:%SZ')
                if created.replace(tzinfo=ZoneInfo('UTC')) >= cutoff:
                    recent += 1
                    senti = (((m.get('entities') or {}).get('sentiment') or {}).get('basic') or '')
                    if senti == 'Bullish':
                        bull += 1
                    elif senti == 'Bearish':
                        bear += 1
            except (KeyError, ValueError):
                pass
        rows.append({
            'symbol': sym, 'source': 'stocktwits', 'trade_date': td,
            'mentions': recent, 'bull_cnt': bull, 'bear_cnt': bear,
            'extra': {
                'sampled': len(msgs),
                'sampled_24h': recent,
                'labeled_24h': bull + bear,
                'window_hours': 24,
            },
        })
        time.sleep(pause)
    status = 'ok' if covered == len(targets) else ('partial' if covered else 'unavailable')
    detail_parts = []
    if cloudflare_blocked:
        detail_parts.append('Cloudflare browser challenge（403），服务端无法采集')
    if rate_limited:
        detail_parts.append('触发 429 限流')
    if errors:
        detail_parts.append(f'{len(errors)} 只请求失败')
    if len(symbols) > max_symbols:
        detail_parts.append(f'单轮上限 {max_symbols} 只')
    return _batch('stocktwits', rows, status, len(targets), covered,
                  '；'.join(detail_parts) or '严格近 24h 窗口')


# ── 采集编排 ─────────────────────────────────────────────────────────────

def collect(monitored: set[str], st_priority: list[str]) -> dict:
    """跑三源并写库。monitored=监控集（大写）；st_priority=StockTwits 采集顺序（持仓在前）。

    返回各源写入条数；任何源失败降级为 0 条，不抛异常。
    """
    from core.database import Database
    raw_batches = [
        fetch_apewisdom(monitored),
        fetch_reddit_posts(monitored),
        fetch_stocktwits(st_priority),
    ]

    # 兼容测试或第三方 monkeypatch 仍返回旧版 list 的情况。
    names = ('apewisdom', 'reddit_posts', 'stocktwits')
    batches = []
    for name, value in zip(names, raw_batches):
        if isinstance(value, list):
            value = _batch(name, value, 'unavailable' if not value else 'ok',
                           len(monitored), len({r['symbol'] for r in value}))
        batches.append(value)
    rows = [row for batch in batches for row in batch['rows']]

    db = Database()
    db.connect()
    n = db.add_social_mentions(rows)
    td = et_trade_date()
    if hasattr(db, 'add_social_collection_runs'):
        db.add_social_collection_runs([{
            'source': batch['source'],
            'trade_date': td,
            'status': batch['status'],
            'requested_count': batch['requested_count'],
            'covered_count': batch['covered_count'],
            'row_count': batch['row_count'],
            'detail': batch.get('detail', ''),
        } for batch in batches])
    pruned = db.prune_social_mentions(keep_days=90)
    db.close()
    counts = {batch['source']: batch['row_count'] for batch in batches}
    statuses = {batch['source']: batch['status'] for batch in batches}
    _logger.info(f"[social] 采集完成：apewisdom {counts['apewisdom']} / "
                 f"reddit {counts['reddit_posts']} / stocktwits {counts['stocktwits']}，"
                 f'入库 {n} 条，清理 {pruned} 条旧样本')
    return {**counts, 'saved': n, 'source_status': statuses}
