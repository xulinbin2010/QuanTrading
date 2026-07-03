"""
盘前美股分析简报服务。

设计（用户确认的两条架构决定）：
  ① 应用内接 Claude API：后端拉「实时行情」(yfinance) + 用 web_search 工具检索隔夜
     新闻/财报/美联储/宏观日历，喂给 claude-opus-4-8 直接产出五个模块的完整简报。
  ② 混合持仓来源：ticker/现价/成本可从 IB 持仓导入（前端复用 /portfolio/positions），
     定性字段（持有逻辑/仓位占比/止损位/触发条件）在前端手填，存本地 JSON。

数据时效性硬要求：所有价格/利率/期货数字由本服务实时拉取（标 as-of 时间戳 + 来源），
**不依赖模型记忆**；模型只负责在这些权威数字之上做新闻检索与分析。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
_CONFIG_FILE = ROOT / 'data' / 'premarket_config.json'
_ET = ZoneInfo('America/New_York')

MODEL = 'claude-opus-4-8'

# 模块1 宏观快照标的（隔夜环境）
_MACRO: list[tuple[str, str]] = [
    ('ES=F', 'S&P500 期货 (ES)'),
    ('NQ=F', 'Nasdaq100 期货 (NQ)'),
    ('YM=F', 'Dow 期货 (YM)'),
    ('^VIX', 'VIX 波动率'),
    ('^TNX', '美10年期国债收益率'),
    ('^FVX', '美5年期国债收益率'),
    ('^TYX', '美30年期国债收益率'),
    ('DX-Y.NYB', '美元指数 DXY'),
    ('CL=F', 'WTI 原油'),
    ('GC=F', '黄金'),
    ('^N225', '日经225'),
    ('^HSI', '恒生指数'),
    ('^STOXX50E', '欧洲斯托克50'),
]

_DEFAULT_CONFIG: dict = {'core': [], 'swing': [], 'watchlist': []}


class MissingAPIKey(RuntimeError):
    pass


def _now_et() -> str:
    return datetime.now(_ET).strftime('%Y-%m-%d %H:%M:%S ET')


# ── 配置持久化（持仓 + watchlist 的定性字段）──────────────────────────

def get_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            d = json.loads(_CONFIG_FILE.read_text('utf-8'))
            return {k: d.get(k, []) for k in _DEFAULT_CONFIG}
        except Exception:
            pass
    return {k: [] for k in _DEFAULT_CONFIG}


def save_config(cfg: dict) -> dict:
    clean = {k: (cfg.get(k) or []) for k in _DEFAULT_CONFIG}
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), 'utf-8')
    return clean


# ── 实时行情（yfinance）──────────────────────────────────────────────

def _macro_one(sym: str) -> tuple[str, dict]:
    import yfinance as yf
    try:
        fi = yf.Ticker(sym).fast_info
        last = float(fi.last_price)
        prev = float(fi.previous_close)
        chg = (last - prev) / prev * 100 if prev else None
        return sym, {
            'last': round(last, 3),
            'prev_close': round(prev, 3),
            'change_pct': round(chg, 2) if chg is not None else None,
        }
    except Exception as e:
        return sym, {'last': None, 'prev_close': None, 'change_pct': None, 'error': str(e)[:60]}


def get_market_snapshot() -> dict:
    syms = [s for s, _ in _MACRO]
    res: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, q in ex.map(_macro_one, syms):
            res[sym] = q
    rows = [{'symbol': s, 'label': lbl, **res.get(s, {})} for s, lbl in _MACRO]
    return {'as_of': _now_et(), 'source': 'yfinance', 'rows': rows}


def _stock_one(sym: str) -> tuple[str, dict]:
    import yfinance as yf
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        last = float(fi.last_price)
        prev = float(fi.previous_close)
        chg = (last - prev) / prev * 100 if prev else None
        hi20 = lo20 = None
        try:
            h = t.history(period='2mo')
            if h is not None and not h.empty:
                hi20 = round(float(h['High'].tail(20).max()), 2)
                lo20 = round(float(h['Low'].tail(20).min()), 2)
        except Exception:
            pass
        return sym, {
            'last': round(last, 2),
            'prev_close': round(prev, 2),
            'change_pct': round(chg, 2) if chg is not None else None,
            'high_20d': hi20, 'low_20d': lo20,
        }
    except Exception as e:
        return sym, {'error': str(e)[:60]}


def get_quotes(symbols: list[str]) -> dict:
    syms = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    out: dict = {}
    if syms:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, q in ex.map(_stock_one, syms):
                out[sym] = q
    return {'as_of': _now_et(), 'source': 'yfinance', 'quotes': out}


def get_scan() -> dict:
    """纯实时数据看板：宏观快照 + 配置清单 + 各标的盘前报价（无 LLM）。"""
    cfg = get_config()
    return {
        'snapshot': get_market_snapshot(),
        'config': cfg,
        'quotes': get_quotes(_all_symbols(cfg)),
    }


# ── prompt 组装 ──────────────────────────────────────────────────────

_SYSTEM = """你是盘前美股分析助手。现在是美东时间开盘前，请基于「下方实时数据区」生成今日盘前简报。

# 数据时效性要求（强制）
- 价格/利率/期货/VIX/指数等行情数字，一律以「下方实时数据区」给出的数值为准（已标注 as-of 时间戳与来源 yfinance）；**禁止使用你记忆中的旧数字，禁止自行编造价格**。
- 隔夜财报、宏观数据日历(含 consensus)、美联储官员讲话、地缘/政策新闻，**必须用 web_search 工具实时检索后再写**，标注来源与时间；检索不到的明确写「未检索到」。
- Pre-market 报价流动性稀薄，可能与开盘价显著偏离，需注明 thin-liquidity caveat。
- 区分 official close（昨日已定）与 overnight move（隔夜新增）。

# 输出结构（严格按模块，用 Markdown，模块标题用 ## ）
## 【模块1】隔夜环境快照
- 三大期货(ES/NQ/YM) overnight 变动%、10Y/30Y 收益率、DXY、WTI/Gold、亚欧盘(Nikkei/HSI/STOXX)、VIX，均引用实时数据区数字
- 末尾一句话定调：risk-on / risk-off / 中性
## 【模块2】隔夜重大事件（web_search）
- 隔夜财报(盘后/盘前) + 市场反应；今日宏观数据(时间ET + consensus)；美联储/地缘/政策
- 仅列对我持仓/watchlist 有传导的事件，标注影响标的
## 【模块3】个股盘前扫描
- 逐个过 持仓 + watchlist：pre-market 价 + 涨跌幅(标 thin liquidity)、异动原因、关键技术位(昨收/support/resistance)、今日相关 catalyst
## 【模块4】今日行动清单
- 按「核心持仓 / 短线仓位 / watchlist」分组；每条给「做什么 + 为什么 + 关键价位」，不要模糊建议
## 【模块5】今日关键时点
- 经济数据发布时间(ET)、今日盘后/明日盘前相关财报、需盯盘的时间窗口

# 风格
- 信息密度优先，去客套与泛泛而谈；所有判断附数据支撑，不确定的明确说「不确定」。
- 技术术语用英文，分析用中文。
- 最后用 3 行以内给出今日整体 takeaway。"""


def _fmt_chg(q: dict) -> str:
    c = q.get('change_pct')
    return f"{c:+.2f}%" if isinstance(c, (int, float)) else '—'


def _quote_inline(sym: str, quotes: dict) -> str:
    q = (quotes.get('quotes') or {}).get(sym.upper()) or {}
    if q.get('error') or q.get('last') is None:
        return 'pre-mkt 报价缺失'
    parts = [f"pre-mkt {q['last']} ({_fmt_chg(q)})", f"昨收 {q.get('prev_close')}"]
    if q.get('high_20d') is not None:
        parts.append(f"20d高/低 {q['high_20d']}/{q['low_20d']}")
    return ' · '.join(parts)


def _build_user(cfg: dict, snapshot: dict, quotes: dict) -> str:
    L: list[str] = [f"当前美东时间：{_now_et()}", '']
    # 实时宏观快照表
    L.append(f"## 实时宏观快照（as-of {snapshot['as_of']}，来源 yfinance）")
    L.append('| 指标 | 现价 | 隔夜涨跌% | 昨结/昨收 |')
    L.append('|---|---|---|---|')
    for r in snapshot['rows']:
        last = r.get('last') if r.get('last') is not None else '—'
        L.append(f"| {r['label']} | {last} | {_fmt_chg(r)} | {r.get('prev_close', '—')} |")
    L.append('')
    # 持仓 + watchlist + 实时报价
    L.append(f"## 我的持仓与关注清单（盘前报价 as-of {quotes['as_of']}，来源 yfinance）")
    core = cfg.get('core') or []
    swing = cfg.get('swing') or []
    wl = cfg.get('watchlist') or []
    L.append('### 核心持仓（medium-to-long）')
    if not core:
        L.append('（无）')
    for h in core:
        t = (h.get('ticker') or '').upper()
        L.append(f"- {t} | 成本 {h.get('cost', '—')} | 仓位 {h.get('weight', '—')} | 逻辑：{h.get('thesis', '—')} | {_quote_inline(t, quotes)}")
    L.append('### 短线仓位（swing）')
    if not swing:
        L.append('（无）')
    for h in swing:
        t = (h.get('ticker') or '').upper()
        L.append(f"- {t} | 进场 {h.get('entry', '—')} | 止损 {h.get('stop', '—')} | 理由：{h.get('reason', '—')} | {_quote_inline(t, quotes)}")
    L.append('### Watchlist（待入场）')
    if not wl:
        L.append('（无）')
    for h in wl:
        t = (h.get('ticker') or '').upper()
        L.append(f"- {t} | 触发条件：{h.get('trigger', '—')} | 理由：{h.get('reason', '—')} | {_quote_inline(t, quotes)}")
    L.append('')
    L.append('请先用 web_search 检索隔夜财报/市场反应、今日宏观数据日历(consensus)、美联储官员讲话、地缘政策新闻，再生成今日盘前简报。')
    return '\n'.join(L)


def _all_symbols(cfg: dict) -> list[str]:
    out: list[str] = []
    for grp in ('core', 'swing', 'watchlist'):
        for h in (cfg.get(grp) or []):
            t = (h.get('ticker') or '').strip()
            if t:
                out.append(t)
    return out


# ── 调 Claude 生成简报 ───────────────────────────────────────────────

def generate_briefing() -> dict:
    import os
    try:  # 重新读 .env，加完 key 无需重启服务即可生效
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except Exception:
        pass
    if not os.environ.get('ANTHROPIC_API_KEY'):
        raise MissingAPIKey(
            '未配置 ANTHROPIC_API_KEY。请在项目根目录 .env 中加入 ANTHROPIC_API_KEY=sk-ant-...，'
            '并确认已 pip install anthropic（重启 Web 服务后生效）。')
    try:
        import anthropic
    except ImportError:
        raise MissingAPIKey('未安装 anthropic SDK。请在 .venv 中执行：pip install anthropic')

    cfg = get_config()
    snapshot = get_market_snapshot()
    quotes = get_quotes(_all_symbols(cfg))
    user = _build_user(cfg, snapshot, quotes)

    client = anthropic.Anthropic()
    tools = [{'type': 'web_search_20260209', 'name': 'web_search'}]
    messages: list = [{'role': 'user', 'content': user}]

    final = None
    for _ in range(6):  # pause_turn 安全循环（server tool 迭代上限）
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=_SYSTEM,
            thinking={'type': 'adaptive'},
            output_config={'effort': 'high'},
            tools=tools,
            messages=messages,
        ) as stream:
            final = stream.get_final_message()
        if final.stop_reason != 'pause_turn':
            break
        messages.append({'role': 'assistant', 'content': final.content})

    text = '\n'.join(b.text for b in final.content if getattr(b, 'type', None) == 'text').strip()
    if final.stop_reason == 'refusal':
        text = '（模型因安全策略未生成内容，请调整持仓清单或稍后重试。）'
    usage = getattr(final, 'usage', None)
    return {
        'briefing': text,
        'as_of': snapshot['as_of'],
        'model': getattr(final, 'model', MODEL),
        'snapshot': snapshot,
        'quotes': quotes,
        'usage': {
            'input_tokens': getattr(usage, 'input_tokens', None),
            'output_tokens': getattr(usage, 'output_tokens', None),
        } if usage else None,
    }
