"""定向个股情报引擎（Claude + web_search）— 两条业务线共用：

  ① 出场待确认情报（exit intel）：auto_trader 触发 -15%/EMA21 出场后不再直接卖，
     改挂「待确认出场」记录；本模块对触发标的检索个股新闻/产业链龙头动向，并判断
     下跌属于「个股利空」还是「板块/外围系统性恐慌」，辅助人工决定卖/留。
  ② 核心票每日情报卡（core cards）：对盘前简报 core 组每只票做定向检索出一张深度卡
     （隔夜要闻/产业链同行/华尔街动向/催化剂），并对照手填的持有逻辑与失效条件
     给出论点检查结论（强化/中性/削弱/失效预警）。缓存 data/.core_cards_cache.json。

双引擎（自动选择，`INTEL_ENGINE` 环境变量可强制 cli/api）：
  - **cli**（默认优先）：本机 `claude` CLI 无头模式（-p + WebSearch 工具），走 Claude **订阅**
    额度（OAuth），不消耗 API 余额。子进程会剥掉 ANTHROPIC_API_KEY 防止误走 API 计费；
    模型默认 sonnet（INTEL_CLI_MODEL 可改）。
  - **api**：Anthropic API（claude-opus-4-8 + web_search server tool），需 API key 且有余额。
所有实时价格由 yfinance 拉取（复用 premarket_svc.get_quotes），禁止模型编造价格。
两个引擎都不可用时抛 MissingAPIKey，调用方优雅降级（出场确认流程不依赖情报）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from web.services.premarket_svc import MissingAPIKey, get_quotes, _now_et

ROOT = Path(__file__).resolve().parents[2]
_CARDS_CACHE = ROOT / 'data' / '.core_cards_cache.json'
_ET = ZoneInfo('America/New_York')

MODEL = 'claude-opus-4-8'          # API 引擎用
CLI_MODEL_DEFAULT = 'sonnet'       # CLI 引擎用（订阅额度，性价比优先）


# ── 引擎调度 ─────────────────────────────────────────────────────────

def _cli_path() -> str | None:
    import os
    import shutil
    exe = shutil.which('claude')
    if exe:
        return exe
    fallback = os.path.expanduser('~/.local/bin/claude')
    return fallback if os.path.exists(fallback) else None


def _call_claude(system: str, user: str, max_tokens: int = 12000) -> tuple[str, dict | None]:
    """按引擎优先级调用 Claude。auto（默认）：先订阅 CLI（零边际成本），失败/未装再试 API。"""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except Exception:
        pass
    engine = (os.environ.get('INTEL_ENGINE') or 'auto').lower()
    cli = _cli_path()
    has_key = bool(os.environ.get('ANTHROPIC_API_KEY'))

    if engine == 'api':
        return _call_claude_api(system, user, max_tokens)
    if engine == 'cli':
        return _call_claude_cli(system, user)
    # auto：订阅 CLI 优先，API 兜底
    if cli:
        try:
            return _call_claude_cli(system, user)
        except Exception as e:
            if not has_key:
                raise
            print(f'  [情报] CLI 引擎失败（{e}），降级尝试 API…')
            return _call_claude_api(system, user, max_tokens)
    if has_key:
        return _call_claude_api(system, user, max_tokens)
    raise MissingAPIKey(
        '没有可用的 Claude 引擎：本机未找到 claude CLI（订阅），也未配置 ANTHROPIC_API_KEY。'
        '装有 Claude Code 的机器无需任何配置即可用订阅引擎。')


def _call_claude_cli(system: str, user: str) -> tuple[str, dict | None]:
    """claude CLI 无头模式：走订阅额度（OAuth），WebSearch 做联网检索。"""
    import os
    import subprocess
    import tempfile
    exe = _cli_path()
    if not exe:
        raise MissingAPIKey('本机未安装 claude CLI（Claude Code）。')
    env = dict(os.environ)
    env.pop('ANTHROPIC_API_KEY', None)   # 关键：强制订阅 OAuth，防止误烧 API 余额
    model = os.environ.get('INTEL_CLI_MODEL', CLI_MODEL_DEFAULT)
    prompt = f'{system}\n\n---\n\n{user}'
    r = subprocess.run(
        [exe, '-p', prompt, '--allowedTools', 'WebSearch', '--model', model],
        capture_output=True, text=True, timeout=900,
        env=env, cwd=tempfile.gettempdir(),   # 中性 cwd：不加载本项目 CLAUDE.md，省上下文
    )
    if r.returncode != 0:
        raise RuntimeError(f'claude CLI 退出码 {r.returncode}：{(r.stderr or r.stdout)[:300]}')
    text = r.stdout.strip()
    if not text:
        raise RuntimeError('claude CLI 无输出')
    return text, {'engine': f'cli/{model}'}


# ── API 引擎（含 pause_turn 循环，与 premarket_svc 对齐）─────────────────

def _call_claude_api(system: str, user: str, max_tokens: int = 12000) -> tuple[str, dict | None]:
    import os
    if not os.environ.get('ANTHROPIC_API_KEY'):
        raise MissingAPIKey(
            '未配置 ANTHROPIC_API_KEY。请在项目根目录 .env 中加入 ANTHROPIC_API_KEY=sk-ant-...，'
            '并确认账户有可用额度。')
    try:
        import anthropic
    except ImportError:
        raise MissingAPIKey('未安装 anthropic SDK。请在 .venv 中执行：pip install anthropic')

    client = anthropic.Anthropic()
    tools = [{'type': 'web_search_20260209', 'name': 'web_search'}]
    messages: list = [{'role': 'user', 'content': user}]

    final = None
    for _ in range(6):  # pause_turn 安全循环（server tool 迭代上限）
        with client.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            thinking={'type': 'adaptive'},
            tools=tools,
            messages=messages,
        ) as stream:
            final = stream.get_final_message()
        if final.stop_reason != 'pause_turn':
            break
        messages.append({'role': 'assistant', 'content': final.content})

    text = '\n'.join(b.text for b in final.content if getattr(b, 'type', None) == 'text').strip()
    if final.stop_reason == 'refusal':
        text = '（模型因安全策略未生成内容）'
    usage = getattr(final, 'usage', None)
    usage_d = {
        'input_tokens': getattr(usage, 'input_tokens', None),
        'output_tokens': getattr(usage, 'output_tokens', None),
    } if usage else None
    return text, usage_d


def _split_blocks(text: str, symbols: list[str]) -> dict[str, str]:
    """按【SYMBOL】行把输出切成逐股块；找不到标记的股票返回空串。"""
    out: dict[str, str] = {s: '' for s in symbols}
    # 标记行位置：【NVDA】（容忍前后空白）
    marks: list[tuple[int, str]] = []
    for m in re.finditer(r'^\s*【([A-Z][A-Z0-9.\-]{0,9})】\s*$', text, re.MULTILINE):
        sym = m.group(1).upper()
        if sym in out:
            marks.append((m.start(), sym))
    for i, (pos, sym) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        block = text[pos:end].strip()
        # 去掉首行标记本身
        block = re.sub(r'^\s*【[A-Z][A-Z0-9.\-]{0,9}】\s*\n?', '', block).strip()
        out[sym] = block
    return out


# ══════════════════════════════════════════════════════════════════════
# ① 出场待确认情报
# ══════════════════════════════════════════════════════════════════════

_EXIT_SYSTEM = """你是美股持仓风控助手。我的量化系统对以下持仓触发了出场信号（止损/趋势破位），\
但在真正卖出前需要人工确认。请对每只触发标的做快速情报检索，帮助我判断该卖还是该留。

# 检索要求（每只标的都要做，用 web_search 实时检索）
1. 个股层面：最近 3 个交易日该公司有什么新闻/财报/指引/评级变化？下跌是否有个股自身的基本面原因？
2. 板块层面：同产业链龙头近两天走势（例：存储链看 SK 海力士/三星/MU，GPU 链看 NVDA/AMD/TSM，\
光模块看 AVGO/COHR 等，按标的实际板块选参照）。龙头是否已企稳/反弹？
3. 市场层面：这次下跌是否属于大盘/外围系统性恐慌（VIX 飙升、宏观数据、地缘事件）带崩，而非个股问题？

# 数据纪律
- 价格/涨跌幅一律以「实时数据区」数字为准，禁止用记忆中的旧价格。
- 检索不到的信息明确写「未检索到」，不要编造。

# 输出格式（严格遵守）
对每只标的输出一个块，块的第一行固定为【代码】单独一行，然后用短横线列表：
- 下跌归因：个股利空 / 板块拖累 / 系统性恐慌 / 无明显消息（选一个主标签，跟 2-3 句依据+来源）
- 龙头动向：同链龙头近两日表现与信号
- 关键事件：即将到来的财报/催化剂（若有，标日期）
- 倾向建议：卖出 / 保留 / 减半观察 — 一句话理由（仅供参考，最终由人决定）
纯文本+短横线列表，不用表格、不用 # 标题。全部标的写完后不要再加总结。"""


def generate_exit_intel(items: list[dict]) -> dict:
    """对触发出场的标的生成情报。items: [{symbol, avg_cost, cur_price, ret, reason}]
    返回 {'as_of', 'model', 'per_symbol': {sym: text}, 'usage'}"""
    symbols = [str(it['symbol']).upper() for it in items]
    quotes = get_quotes(symbols)

    L: list[str] = [f'当前美东时间：{_now_et()}', '', '## 触发出场的持仓']
    for it in items:
        sym = str(it['symbol']).upper()
        q = (quotes.get('quotes') or {}).get(sym) or {}
        live = f"实时 {q.get('last', '—')}（{q.get('change_pct', '—')}%）昨收 {q.get('prev_close', '—')}" \
            if not q.get('error') else '实时报价缺失'
        L.append(f"- {sym} | 成本 {it.get('avg_cost', '—')} | 触发价 {it.get('cur_price', '—')} | "
                 f"浮动收益 {it.get('ret', 0):+.1%} | 触发规则：{it.get('reason', '—')} | {live}")
    L += ['', f"## 实时数据区（as-of {quotes['as_of']}，来源 yfinance）",
          json.dumps(quotes.get('quotes') or {}, ensure_ascii=False),
          '', '请对上面每只标的按系统提示的格式输出情报块。']

    text, usage = _call_claude(_EXIT_SYSTEM, '\n'.join(L))
    return {
        'as_of': _now_et(),
        'model': (usage or {}).get('engine', MODEL),
        'per_symbol': _split_blocks(text, symbols),
        'raw': text,
        'usage': usage,
    }


def enrich_pending_exits(db, max_age_hours: float = 20.0) -> int:
    """给 DB 里缺情报（或情报过期）的 pending 出场记录补 Claude 情报。
    供 auto_trader 在下完所有单之后 best-effort 调用；任何异常都不应中断主流程，
    由调用方 try/except。返回补写的记录数。"""
    rows = db.get_pending_exits('pending')
    need = []
    now = datetime.now(_ET)
    for r in rows:
        if not r.get('intel_json'):
            need.append(r)
            continue
        try:
            at = datetime.fromisoformat(str(r.get('intel_at'))).replace(tzinfo=None)
            ref = now.replace(tzinfo=None)
            if (ref - at).total_seconds() > max_age_hours * 3600:
                need.append(r)
        except Exception:
            need.append(r)
    if not need:
        return 0
    items = [{'symbol': r['symbol'], 'avg_cost': r['avg_cost'],
              'cur_price': r['trigger_price'], 'ret': r['ret'] or 0.0,
              'reason': r['reason']} for r in need]
    intel = generate_exit_intel(items)
    n = 0
    for r in need:
        block = intel['per_symbol'].get(str(r['symbol']).upper()) or ''
        if not block:
            continue
        db.set_pending_exit_intel(r['id'], json.dumps(
            {'text': block, 'as_of': intel['as_of'], 'model': intel['model']},
            ensure_ascii=False))
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════════
# ② 核心票每日情报卡
# ══════════════════════════════════════════════════════════════════════

_CARDS_SYSTEM = """你是我的核心持仓每日情报官。下面是我重仓跟踪的核心票清单，每只附有我手填的\
持有逻辑、失效条件、催化剂日历。请对每只票做定向检索，各输出一张情报卡。

# 检索要求（每只都做，用 web_search 实时检索）
1. 近 24-48 小时该公司新闻：财报/指引/产品/大客户/供应链/监管/高管变动
2. 产业链与同行：上下游关键公司与直接竞争对手的动向（涨价/砍单/扩产/新品/技术路线）
3. 华尔街动向：评级/目标价变动、大行观点、显著的多空分歧
4. 催化剂核对：我列的催化剂日期是否临近或有变？有没有我没列到的新催化剂？

# 论点检查（最重要的一步）
对照我手填的「持有逻辑」与「失效条件」，判断最近的信息流对论点属于哪一档：
强化（新证据支持逻辑）/ 中性（无增量信息）/ 削弱（出现反面证据但未触发失效）/ 失效预警（失效条件正在兑现）

# 数据纪律
- 价格一律以「实时数据区」数字为准，禁止用记忆价格；检索不到的写「未检索到」。

# 输出格式（严格遵守）
每只票一个块，块的第一行固定为【代码】单独一行，然后：
- 隔夜要闻：…（附来源；无就写「无重大新闻」）
- 产业链/同行：…
- 华尔街：…
- 催化剂：…（含距今天数）
块的最后一行固定为：论点检查：强化 — 一句话理由（档位在 强化/中性/削弱/失效预警 四选一）
纯文本+短横线列表，不用表格、不用 # 标题，每卡 150 字内保持信息密度。"""

_VERDICTS = ('失效预警', '削弱', '强化', '中性')  # 按匹配优先级


def _extract_verdict(block: str) -> str | None:
    m = re.search(r'论点检查[：:]\s*(失效预警|强化|中性|削弱)', block)
    return m.group(1) if m else None


def generate_core_cards() -> dict:
    """对盘前简报 core 组生成每日情报卡并写缓存。"""
    from web.services.premarket_svc import get_config
    core = [r for r in (get_config().get('core') or []) if (r.get('ticker') or '').strip()]
    if not core:
        raise ValueError('核心票清单为空：请先在「盘前扫描 → 清单配置」的核心持仓组添加股票并保存。')

    symbols = [r['ticker'].strip().upper() for r in core]
    quotes = get_quotes(symbols)

    L: list[str] = [f'当前美东时间：{_now_et()}', '', '## 核心票清单（我手填的跟踪档案）']
    for r in core:
        t = r['ticker'].strip().upper()
        L.append(f"- {t} | 成本 {r.get('cost') or '—'} | 仓位 {r.get('weight') or '—'}\n"
                 f"  持有逻辑：{r.get('thesis') or '（未填）'}\n"
                 f"  失效条件：{r.get('invalidation') or '（未填，请按常识性风险检查）'}\n"
                 f"  催化剂日历：{r.get('catalysts') or '（未填，请检索补充）'}")
    L += ['', f"## 实时数据区（as-of {quotes['as_of']}，来源 yfinance）",
          json.dumps(quotes.get('quotes') or {}, ensure_ascii=False),
          '', '请对每只票按系统提示的格式输出情报卡。']

    text, usage = _call_claude(_CARDS_SYSTEM, '\n'.join(L), max_tokens=16000)
    blocks = _split_blocks(text, symbols)
    cards = [{
        'ticker': s,
        'text': blocks.get(s) or '（本次未生成，可重试）',
        'verdict': _extract_verdict(blocks.get(s) or ''),
    } for s in symbols]

    result = {
        'as_of': _now_et(),
        'generated_at_cn': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
        'model': (usage or {}).get('engine', MODEL),
        'cards': cards,
        'usage': usage,
    }
    try:
        _CARDS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CARDS_CACHE.write_text(json.dumps(result, ensure_ascii=False, indent=1), 'utf-8')
    except Exception:
        pass
    return result


def get_cached_core_cards() -> dict | None:
    if _CARDS_CACHE.exists():
        try:
            return json.loads(_CARDS_CACHE.read_text('utf-8'))
        except Exception:
            return None
    return None


# ── 业务线 3：情报中心（持仓新闻事件提取，/#/intel）───────────────────
#   原始新闻/SEC（core/stock_news.py，免费+2h缓存）→ 一次 Claude 调用聚簇去重、
#   提取结构化事件卡（类型/方向/强度/影响标的/解读）→ 缓存 JSON，页面秒开。

_EVENTS_CACHE = ROOT / 'data' / '.intel_events_cache.json'
_CASH_EQUIV = {'SGOV', 'BIL', 'USFR'}   # 现金等价 ETF，无情报价值
_events_running = False
_events_error: str | None = None

_EVENTS_SYSTEM = """你是一名服务于个人美股波段交易者的买方情报分析师。
输入是他的持仓清单（含手填的持有逻辑）和每只票的原始新闻标题 / SEC 公告列表。
任务：提取值得他知道的「事件」，输出结构化 JSON 事件卡。

规则：
1. 聚簇去重：多条新闻报道同一件事只输出一张事件卡
2. 过滤噪音：纯行情复述（如"股价今日上涨3%"）、营销软文、与持仓无关的泛市场新闻一律丢弃
3. 产业链传导要显式标注：如 SK 海力士涨价 → 利好 MU（存储涨价周期）；影响写进 symbols 和 analysis
   持仓含杠杆/篮子 ETF 时（清单里标了底层标的），symbols 同时写底层和他实际持有的代码（如 ["MU","MUU"]），
   analysis 里提醒杠杆产品波动会被放大
4. 可用 WebSearch 补充：新闻标题信息不足以判断方向时检索确认；持仓所在产业链的重大事件
   （竞对/上下游/大客户动向）若原始列表没有、你检索到了也应补卡
5. analysis 必须回答"这对他的持仓意味着什么、需不需要行动"，不要复述新闻
6. 候选池（非持仓的 AI 股票池）的新闻只保留**强事件**：财报暴雷/大超预期、重大订单合作、
   指引大幅调整、监管/诉讼、并购。日常波动、评级微调、软文一律丢弃；宁缺毋滥
7. 输出严格 JSON，除 JSON 外不要输出任何文字：
{"events": [{
  "scope": "持仓|候选池",
  "type": "财报|指引|产能价格|监管|大单合作|管理层|产业链|评级|其他",
  "symbols": ["MU"],
  "direction": "利好|利空|中性",
  "strength": "强|中|弱",
  "title": "一句话事件标题（中文）",
  "analysis": "一两句解读：对持仓/候选的意义 + 是否需要行动",
  "date": "YYYY-MM-DD",
  "links": ["https://..."]
}]}
按重要性降序；持仓事件最多 20 张，候选池事件最多 8 张。"""


# 杠杆/篮子 ETF → 底层标的（新闻按底层拉，否则 MUU 这类代码拉不到有效新闻）
_UNDERLYING: dict[str, list[str]] = {
    'MUU':  ['MU'],  'MULL': ['MU'],          # 2x 美光
    'ARMG': ['ARM'],                           # 2x ARM
    'RAM':  ['MU', 'SNDK', 'WDC'],             # 2x DRAM 篮子（Roundhill Memory）
    'DRAM': ['MU', 'SNDK', 'WDC'],             # 存储篮子 ETF
    '000660': ['000660.KS'],                   # SK 海力士（韩股，yfinance 后缀）
}


def _holdings_for_intel() -> list[dict]:
    """持仓清单 = 实盘诊断最近录入 ∪ IB 模拟持仓；thesis 从盘前 core 组按 symbol 补齐。"""
    from web.services.premarket_svc import get_config
    thesis_map = {
        (r.get('ticker') or '').strip().upper(): (r.get('thesis') or '').strip()
        for r in (get_config().get('core') or [])
    }
    rows: dict[str, dict] = {}
    # ① 实盘诊断（桌面医生）最近一次录入的真实账户持仓
    try:
        from web.services.account_doctor_svc import get_latest
        for p in ((get_latest() or {}).get('positions') or []):
            sym = str(p.get('symbol') or '').strip().upper()
            if sym and sym != '?' and sym not in _CASH_EQUIV:
                lev = p.get('leverage_factor') or 1
                rows[sym] = {
                    'symbol': sym, 'name': p.get('name') or '', 'theme': p.get('theme') or '',
                    'leverage': lev, 'thesis': thesis_map.get(sym, ''), 'source': '实盘',
                }
    except Exception:
        pass
    # ② IB 模拟持仓（Gateway 在线时 best-effort 合并）
    try:
        from web.services.portfolio_svc import get_positions
        for p in get_positions():
            sym = str(p.get('symbol') or '').upper()
            # 只并入普通股票代码；期权组合串（含空格/数字）与现金等价 ETF 跳过
            if sym and sym.isalpha() and sym not in _CASH_EQUIV and sym not in rows:
                rows[sym] = {'symbol': sym, 'name': '', 'theme': '', 'leverage': 1,
                             'thesis': thesis_map.get(sym, ''), 'source': '模拟'}
    except Exception:
        pass
    return list(rows.values())


def _news_symbols(holds: list[dict]) -> list[str]:
    """新闻检索标的：持仓代码经底层映射展开去重（杠杆 ETF → 底层个股）。"""
    seen: list[str] = []
    for h in holds:
        for s in _UNDERLYING.get(h['symbol'], [h['symbol']]):
            if s not in seen:
                seen.append(s)
    return seen


def _pool_symbols(covered: set[str]) -> list[str]:
    """候选池：AI 股票池可见分组成员，去掉已被持仓（含底层展开）覆盖的。"""
    try:
        uni = json.loads((ROOT / 'data' / 'ai_universe.json').read_text('utf-8'))
    except Exception:
        return []
    out: list[str] = []
    for g in (uni.get('groups') or {}).values():
        if g.get('hidden'):
            continue
        for s in (g.get('symbols') or []):
            s = str(s).upper()
            if s not in covered and s not in out:
                out.append(s)
    return out


def _parse_events_json(text: str) -> list[dict]:
    """容忍 markdown 代码块 / 前后杂文字，抽出最外层 JSON 并规范字段。"""
    t = text.strip()
    for cand in (t, re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.M)):
        try:
            data = json.loads(cand)
            break
        except Exception:
            data = None
    if data is None:
        m = re.search(r'\{.*\}', t, re.S)
        if not m:
            raise RuntimeError(f'模型输出不是 JSON：{t[:200]}')
        data = json.loads(m.group(0))
    out = []
    for e in (data.get('events') or []):
        out.append({
            'scope':     '候选池' if str(e.get('scope') or '') == '候选池' else '持仓',
            'type':      str(e.get('type') or '其他'),
            'symbols':   [str(s).upper() for s in (e.get('symbols') or [])],
            'direction': str(e.get('direction') or '中性'),
            'strength':  str(e.get('strength') or '中'),
            'title':     str(e.get('title') or '').strip(),
            'analysis':  str(e.get('analysis') or '').strip(),
            'date':      str(e.get('date') or ''),
            'links':     [str(u) for u in (e.get('links') or []) if u],
        })
    return [e for e in out if e['title']]


def generate_news_events() -> dict:
    """拉持仓原始新闻 → Claude 提取事件卡 → 写缓存。耗时约 1-3 分钟（一次 LLM 调用）。"""
    from core.stock_news import get_stock_news
    holds = _holdings_for_intel()
    if not holds:
        raise ValueError('持仓清单为空：请先在「实盘诊断」录入持仓并诊断一次（或启动 IB Gateway）。')

    L: list[str] = [f'当前美东时间：{_now_et()}', '', '## 持仓清单']
    for h in holds:
        under = _UNDERLYING.get(h['symbol'])
        extra = f"，底层标的 {'/'.join(under)}" if under else ''
        lev = f"，{h['leverage']}x 杠杆" if (h.get('leverage') or 1) > 1 else ''
        name = f"（{h['name']}）" if h.get('name') else ''
        L.append(f"- {h['symbol']}{name}[{h['source']}]{lev}{extra} 持有逻辑：{h['thesis'] or '（未填）'}")
    L += ['', '## 原始新闻 / SEC 公告（按底层标的展开，来源 yfinance + SEC EDGAR）']
    for sym in _news_symbols(holds):
        L.append(f'### {sym}')
        try:
            d = get_stock_news(sym)
        except Exception as e:
            L.append(f'（拉取失败：{e}）')
            continue
        ne = (d.get('analyst') or {}).get('next_earnings')
        if ne:
            L.append(f"- 下次财报：{json.dumps(ne, ensure_ascii=False, default=str)}")
        for f in (d.get('sec_filings') or [])[:5]:
            L.append(f"- [SEC {f.get('form')}] {f.get('date')} {f.get('label') or ''} {f.get('snippet') or ''} {f.get('url')}".strip())
        for n in (d.get('news') or []):
            L.append(f"- [{n.get('publisher')}] {n.get('date')} {n.get('title')} {n.get('url')}")

    # 候选池圈层：AI 股票池（非持仓），仅 yfinance 标题，只让模型留强事件
    from core.stock_news import get_news_light_batch
    covered = {h['symbol'] for h in holds} | set(_news_symbols(holds))
    pool = _pool_symbols(covered)
    if pool:
        L += ['', '## 候选池新闻（AI 股票池，非持仓；只提取强事件，宁缺毋滥）']
        pool_news = get_news_light_batch(pool)
        for sym in pool:
            items = (pool_news.get(sym) or [])[:4]
            if not items:
                continue
            L.append(f'### {sym}')
            for n in items:
                L.append(f"- [{n.get('publisher')}] {n.get('date')} {n.get('title')} {n.get('url')}")

    text, usage = _call_claude(_EVENTS_SYSTEM, '\n'.join(L), max_tokens=16000)
    events = _parse_events_json(text)
    result = {
        'as_of': _now_et(),
        'generated_at_cn': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
        'model': (usage or {}).get('engine', MODEL),
        'holdings': [h['symbol'] for h in holds],
        'events': events,
        'usage': usage,
    }
    try:
        _EVENTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _EVENTS_CACHE.write_text(json.dumps(result, ensure_ascii=False, indent=1), 'utf-8')
    except Exception:
        pass
    return result


def get_cached_news_events() -> dict | None:
    if _EVENTS_CACHE.exists():
        try:
            return json.loads(_EVENTS_CACHE.read_text('utf-8'))
        except Exception:
            return None
    return None


def refresh_news_events_async() -> bool:
    """后台线程跑 generate_news_events。已在跑返回 False。"""
    global _events_running, _events_error
    if _events_running:
        return False
    _events_running = True
    _events_error = None

    def _run():
        global _events_running, _events_error
        try:
            generate_news_events()
        except Exception as e:
            _events_error = str(e)
        finally:
            _events_running = False

    import threading
    threading.Thread(target=_run, daemon=True, name='intel-events').start()
    return True


def news_events_status() -> dict:
    return {'running': _events_running, 'error': _events_error}


# ── AI 池新股发现（补指数扫描的 IPO 盲区）───────────────────────────────

_IPO_DISCOVER_SYSTEM = """你是美股 AI 硬件产业链研究员。用 WebSearch 检索，找出符合以下全部条件的美股上市公司：

1. 属于 AI 硬件产业链：算力芯片/半导体设备材料/存储/服务器/光模块与网络/电源电力/液冷/IDC 算力租赁等。
   **排除纯软件/SaaS/互联网应用公司**。
2. 满足其一：近 12 个月内 IPO 或分拆上市；或虽上市较久但近 6 个月才因 AI 需求成为市场热点。
3. 美股主板上市（NYSE/NASDAQ），市值大致在 $2B–$500B 区间。
4. 不在下方「已收录/已排除」清单中。

重点检索方向：近期 AI/半导体领域 IPO、AI 算力租赁（neocloud）、光模块/CPO、电力设备新贵。
宁缺毋滥：只要有真实业务和产业链卡位的公司，概念蹭热点、未量产的微盘股不要。最多 10 只，没有就返回空数组。

只输出 JSON（不要 markdown 代码块、不要多余文字）：
{"candidates": [{"symbol": "代码", "name": "公司名", "listed": "上市时间(YYYY-MM，非新股填约值)",
  "suggest_group": "分组key(从下方分组表选最贴近的)", "reason": "一句话：业务卡位+为何是新鲜血液(中文,≤40字)"}]}"""


def discover_new_ipos() -> dict:
    """Claude+联网检索 AI 硬件链新票（IPO/新热点）→ yfinance 校验 → 写入 pending_review 待审核。

    与 ai_tracker_svc.auto_discover（指数扫描）互补：指数扫描覆盖不了上市 6-12 个月内
    还没进任何指数的新股（ARM/ALAB/CRWV/NBIS 类）。所有候选一律进待审核，人工把关。
    """
    from web.services.ai_tracker_svc import load_universe, save_universe, analyze_symbol

    u = load_universe()
    existing: set[str] = set()
    for gv in u.get('groups', {}).values():
        existing.update(str(s).upper() for s in gv.get('symbols', []))
    existing.update(str(p.get('symbol', '')).upper() for p in u.get('pending_review', []))
    existing.update(str(s).upper() for s in u.get('rejected', []))

    groups_desc = '\n'.join(
        f"- {k}: {v.get('label', k)}" for k, v in u.get('groups', {}).items())
    user_msg = (f"当前美东时间：{_now_et()}\n\n## 分组表（suggest_group 从这里选）\n{groups_desc}\n\n"
                f"## 已收录/已排除（不要返回这些）\n{', '.join(sorted(existing))}")

    text, usage = _call_claude(_IPO_DISCOVER_SYSTEM, user_msg, max_tokens=8000)

    # 容忍 markdown 代码块 / 前后杂文字
    t = text.strip()
    data = None
    for cand in (t, re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.M)):
        try:
            data = json.loads(cand)
            break
        except Exception:
            continue
    if data is None:
        m = re.search(r'\{.*\}', t, re.S)
        if not m:
            raise RuntimeError(f'模型输出不是 JSON：{t[:200]}')
        data = json.loads(m.group(0))

    valid_groups = set(u.get('groups', {}).keys())
    added, skipped = [], []
    for c in (data.get('candidates') or [])[:10]:
        sym = str(c.get('symbol') or '').upper().strip()
        if not sym or sym in existing:
            continue
        # yfinance 逐只校验：代码真实存在、市值在区间内（新股数据缺失时放行，交人工判断）
        try:
            info = analyze_symbol(sym)
        except Exception as e:
            skipped.append(f'{sym}(校验失败:{e})')
            continue
        if not info.get('sector') and not info.get('industry') and info.get('market_cap_b') is None:
            skipped.append(f'{sym}(yfinance 查不到，可能代码有误)')
            continue
        cap = info.get('market_cap_b')
        if cap is not None and (cap < 2.0 or cap > 500.0):
            skipped.append(f'{sym}(市值 ${cap:.1f}B 超出 $2B–$500B)')
            continue
        # 分组：yfinance 行业推断优先，缺数据时用模型建议兜底
        sg = info.get('suggest_group') or c.get('suggest_group')
        if sg not in valid_groups:
            sg = None
        entry = {
            'symbol':         sym,
            'name':           str(c.get('name') or ''),
            'suggest_group':  sg,
            'note':           f"IPO发现·{c.get('listed') or '?'}上市·{str(c.get('reason') or '').strip()}",
            'industry':       info.get('industry'),
            'sector':         info.get('sector'),
            'market_cap_b':   cap,
            'revenue_growth': info.get('revenue_growth'),
            'discovered_at':  datetime.now().isoformat(timespec='seconds'),
        }
        u.setdefault('pending_review', []).append(entry)
        existing.add(sym)
        added.append(entry)

    if added:
        save_universe(u)
    return {'added': added, 'skipped': skipped,
            'model': (usage or {}).get('engine', MODEL)}


# ── CLI（供调度任务：盘前自动生成核心票情报卡）─────────────────────────
if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='定向个股情报引擎')
    ap.add_argument('--core-cards', action='store_true', help='生成核心票每日情报卡并写缓存')
    ap.add_argument('--news-events', action='store_true', help='生成情报中心持仓事件卡并写缓存')
    ap.add_argument('--ipo-discover', action='store_true',
                    help='Claude 联网检索 AI 硬件链新股/新热点，写入 AI 池待审核列表')
    args = ap.parse_args()
    if args.ipo_discover:
        try:
            res = discover_new_ipos()
            print(f"IPO 发现完成（引擎 {res['model']}）：新增待审核 {len(res['added'])} 只")
            for e in res['added']:
                cap = e.get('market_cap_b')
                cap_str = f"${cap:.1f}B" if cap else '-'
                print(f"  {e['symbol']:<6} {cap_str:>8}  {e['note']}")
            for s in res['skipped']:
                print(f"  [跳过] {s}")
        except MissingAPIKey as e:
            print(f'[跳过] {e}')
            sys.exit(0)
        except Exception as e:
            print(f'[失败] IPO 发现失败：{e}')
            sys.exit(1)
    elif args.news_events:
        try:
            res = generate_news_events()
            print(f"情报中心事件卡已生成（{len(res['events'])} 张，覆盖 {len(res['holdings'])} 只持仓，as-of {res['as_of']}）")
        except (MissingAPIKey, ValueError) as e:
            print(f'[跳过] {e}')
            sys.exit(0)
        except Exception as e:
            print(f'[失败] 事件卡生成失败：{e}')
            sys.exit(1)
    elif args.core_cards:
        try:
            res = generate_core_cards()
            print(f"核心票情报卡已生成（{len(res['cards'])} 只，as-of {res['as_of']}）")
            for c in res['cards']:
                print(f"  {c['ticker']}: 论点检查={c['verdict'] or '未解析'}")
        except (MissingAPIKey, ValueError) as e:
            print(f'[跳过] {e}')
            sys.exit(0)  # 配置类问题不算任务失败
        except Exception as e:
            print(f'[失败] 情报卡生成失败：{e}')
            sys.exit(1)  # API 额度不足/网络错误等，标记任务失败便于调度页排查
    else:
        ap.print_help()
