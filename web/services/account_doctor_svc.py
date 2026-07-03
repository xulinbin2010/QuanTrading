"""账户诊断（桌面医生）服务。

用户不接 IB 实盘 API（正式账户隐私考量），改用**截图**把持仓/保证金喂进来：
  ① parse_screenshots：Claude 视觉解析 IB 持仓/余额截图 → 结构化 JSON（持仓 + 保证金字段
     + 每只标的的行业/主题/是否杠杆分类）。用户可在前端表格核对/修改。
  ② diagnose：**纯 Python 确定性计算**（不依赖模型做数值）——集中度、经济敞口 vs 净值、
     杠杆识别、保证金压力测试、风险清单。可复算、可解释。

⚠️ 截图会发送到 Anthropic API（用户已知悉并选择该方式）；诊断结果存本地 data/account_doctor.json，
   不外传第三方。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / 'data' / 'account_doctor.json'
MODEL = 'claude-opus-4-8'

# 每日重置杠杆 ETF 回撤系数：实测 2x（MUU/MU 回撤 = 75.1/42.8 ≈ 1.75，因下行凸性 <2）。
# 折算成每 1 倍杠杆的系数 0.875，故 dd_mult = leverage_factor × 0.875（2x→1.75，3x→2.625），
# 避免与 leverage_factor 重复相乘。
LEV_DD_PER_X = 0.875
# 压力测试相关系数
SAME_THEME_BETA = 1.0     # 与主导风险同主题的标的，随冲击 1:1
CROSS_THEME_BETA = 0.5    # 其它标的：相关但半程
SHOCKS = [0.20, 0.30, 0.40, 0.50]


class MissingAPIKey(Exception):
    pass


# ────────────────────────────── 截图解析（Claude 视觉）──────────────────────────────

_PARSE_SYSTEM = """你是券商持仓截图解析器。用户会给你一张或多张 Interactive Brokers（或类似券商）的
持仓/余额截图。请**只输出一个 JSON 对象**（不要任何解释、不要 markdown 代码围栏），schema 如下：

{
  "account": {
    "net_liq": number|null,            // 净清算价值 (USD)
    "settled_cash": number|null,       // 已结算现金 (USD，可能为负=融资负债)
    "unrealized_pnl": number|null,     // 未实现盈亏 (USD)
    "maint_margin": number|null,       // 维持保证金 (USD)
    "excess_liquidity": number|null,   // 剩余流动性 (USD)
    "buying_power": number|null        // 购买力 (USD)
  },
  "positions": [
    {
      "symbol": "MU",                  // 代码（去交易所后缀）
      "name": "美光科技",               // 简称
      "shares": 40,                    // 持仓股数
      "last_price": 976.63,            // 最新价（原币种）
      "currency": "USD",               // 计价币种
      "market_value_usd": 39065,       // **换算成美元的市值**（非美元请按合理汇率折算）
      "sector": "半导体",              // 行业大类
      "theme": "存储",                 // 更细的风险主题（如 存储/AI算力/半导体设备/电力/其它）
      "is_leveraged": false,           // 是否杠杆/反向 ETF
      "leverage_factor": 1,            // 杠杆倍数（2x ETF=2，普通=1）
      "underlying": "MU"               // 杠杆 ETF 的底层标的；普通股填自己
    }
  ]
}

识别规则：
- 名称含 "2X"/"BULL"/"BEAR"/"LEVERAGE"/"DIREXION DAILY"/"T-REX"/"3X" 的是杠杆 ETF，设 is_leveraged=true、leverage_factor 相应值。
- theme 用于把"同一个赌注"归组：把美光/海力士/闪迪/存储 ETF 都归 "存储"；ARM/半导体设备等归 "半导体"；GPU/AI 算力归 "AI算力"。拿不准填 "其它"。
- 数字去掉千分位逗号。读不到的字段填 null，不要编造。
- market_value_usd 必填（非美元按你已知汇率折算，宁可近似也要给数）。"""


def parse_screenshots(images: list[dict]) -> dict:
    """images: [{'media_type': 'image/png', 'data': <base64 str>}, ...] → 结构化 draft。"""
    try:
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

    content: list = []
    for img in images:
        content.append({
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': img.get('media_type', 'image/png'),
                'data': img['data'],
            },
        })
    content.append({'type': 'text', 'text': '解析这些截图，按 system 里的 schema 只输出 JSON。'})

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=_PARSE_SYSTEM,
        messages=[{'role': 'user', 'content': content}],
    )
    text = '\n'.join(b.text for b in msg.content if getattr(b, 'type', None) == 'text').strip()
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError('模型未返回可解析的 JSON，请重试或改用手动填写。')
    data.setdefault('account', {})
    data.setdefault('positions', [])
    return data


def _extract_json(text: str):
    """从模型输出里抠出 JSON（容忍 ```json 围栏 / 前后杂字）。"""
    text = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if m:
        text = m.group(1)
    else:
        i, j = text.find('{'), text.rfind('}')
        if i != -1 and j != -1:
            text = text[i:j + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


# ────────────────────────────── 诊断（纯 Python）──────────────────────────────

def _f(v):
    """安全转 float，失败返回 None。"""
    try:
        if v is None or v == '':
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def diagnose(payload: dict, persist: bool = True) -> dict:
    """payload = {'account': {...}, 'positions': [...]}（可来自截图解析后用户核对过的表）。"""
    acc_in = payload.get('account') or {}
    pos_in = payload.get('positions') or []

    positions = []
    gross_long = 0.0
    for p in pos_in:
        mv = _f(p.get('market_value_usd'))
        if mv is None:
            sh, px = _f(p.get('shares')), _f(p.get('last_price'))
            mv = (sh * px) if (sh is not None and px is not None) else 0.0
        lev = _f(p.get('leverage_factor')) or 1.0
        is_lev = bool(p.get('is_leveraged')) or lev > 1.0
        theme = (p.get('theme') or '其它').strip() or '其它'
        positions.append({
            'symbol': p.get('symbol') or '?',
            'name': p.get('name') or '',
            'market_value_usd': round(mv, 0),
            'theme': theme,
            'is_leveraged': is_lev,
            'leverage_factor': lev,
            'exposure_usd': round(mv * lev, 0),
            'currency': p.get('currency') or 'USD',
        })
        gross_long += mv

    net_liq = _f(acc_in.get('net_liq')) or (gross_long or 1.0)
    maint = _f(acc_in.get('maint_margin'))
    excess = _f(acc_in.get('excess_liquidity'))
    settled_cash = _f(acc_in.get('settled_cash'))
    unrealized = _f(acc_in.get('unrealized_pnl'))
    maint_rate = (maint / gross_long) if (maint and gross_long) else None

    # 占比
    for p in positions:
        p['pct'] = round(p['market_value_usd'] / net_liq * 100, 1)
    positions.sort(key=lambda x: x['market_value_usd'], reverse=True)

    # 主题聚合
    themes: dict[str, dict] = {}
    for p in positions:
        t = themes.setdefault(p['theme'], {'theme': p['theme'], 'mv_usd': 0.0, 'exposure_usd': 0.0, 'count': 0})
        t['mv_usd'] += p['market_value_usd']
        t['exposure_usd'] += p['exposure_usd']
        t['count'] += 1
    theme_list = sorted(themes.values(), key=lambda x: x['exposure_usd'], reverse=True)
    for t in theme_list:
        t['exposure_pct'] = round(t['exposure_usd'] / net_liq * 100, 1)
        t['mv_pct'] = round(t['mv_usd'] / net_liq * 100, 1)

    total_exposure = sum(p['exposure_usd'] for p in positions)
    lev_positions = [p for p in positions if p['is_leveraged']]

    # 压力测试：冲击敞口最大的主导主题
    dominant = theme_list[0]['theme'] if theme_list else None
    stress = _stress_test(positions, net_liq, gross_long, maint_rate, excess, dominant)

    findings = _findings(positions, theme_list, net_liq, total_exposure,
                         lev_positions, excess, maint, settled_cash, dominant)

    now = datetime.now(ZoneInfo('America/New_York'))
    result = {
        'account': {
            'net_liq': round(net_liq, 0),
            'gross_long': round(gross_long, 0),
            'maint_margin': round(maint, 0) if maint is not None else None,
            'excess_liquidity': round(excess, 0) if excess is not None else None,
            'maint_rate': round(maint_rate * 100, 1) if maint_rate is not None else None,
            'settled_cash': round(settled_cash, 0) if settled_cash is not None else None,
            'unrealized_pnl': round(unrealized, 0) if unrealized is not None else None,
            'total_exposure': round(total_exposure, 0),
            'total_exposure_pct': round(total_exposure / net_liq * 100, 0),
        },
        'positions': positions,
        'themes': theme_list,
        'stress': stress,
        'findings': findings,
        'as_of': now.strftime('%Y-%m-%d %H:%M ET'),
    }
    if persist:
        try:
            STORE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
    return result


def _stress_test(positions, net_liq, gross_long, maint_rate, excess, dominant):
    """按主导主题下行 -20/-30/-40/-50% 逐档算账户净值 + 剩余流动性 + 是否追缴。"""
    if not positions:
        return None
    # 每 1 单位冲击 d 的美元损失系数
    loss_coef = 0.0
    for p in positions:
        beta = SAME_THEME_BETA if p['theme'] == dominant else CROSS_THEME_BETA
        dd_mult = p['leverage_factor'] * LEV_DD_PER_X if p['is_leveraged'] else 1.0
        loss_coef += p['market_value_usd'] * beta * dd_mult
    rows = []
    for d in SHOCKS:
        loss = loss_coef * d
        new_nl = net_liq - loss
        row = {
            'shock': int(d * 100),
            'net_liq': round(new_nl, 0),
            'drawdown_pct': round((new_nl / net_liq - 1) * 100, 0),
        }
        if excess is not None and maint_rate is not None:
            new_long = max(0.0, gross_long - loss)
            new_maint = maint_rate * new_long
            new_excess = new_nl - new_maint
            row['excess_liquidity'] = round(new_excess, 0)
            row['status'] = 'crit' if new_excess <= 0 else ('warn' if new_excess < 0.15 * net_liq else 'good')
        else:
            row['excess_liquidity'] = None
            row['status'] = 'crit' if row['drawdown_pct'] <= -50 else ('warn' if row['drawdown_pct'] <= -30 else 'good')
        rows.append(row)

    trigger = None
    if excess is not None and maint_rate is not None and loss_coef > 0:
        # excess(d) = excess - (1 - maint_rate) * loss_coef * d = 0
        denom = (1 - maint_rate) * loss_coef
        if denom > 0:
            trigger = round(min(excess / denom * 100, 100), 0)
    return {'theme': dominant, 'rows': rows, 'trigger_shock': trigger,
            'lev_dd_per_x': LEV_DD_PER_X, 'same_beta': SAME_THEME_BETA, 'cross_beta': CROSS_THEME_BETA}


def _findings(positions, theme_list, net_liq, total_exposure,
              lev_positions, excess, maint, settled_cash, dominant):
    """规则化风险清单（可解释）。severity: crit/warn/good。"""
    out = []
    if not positions:
        return out

    # 1. 单一主题集中度
    if theme_list:
        top = theme_list[0]
        pct = top['exposure_pct']
        if pct >= 80:
            out.append(_fd('crit', f'单一主题集中：{top["theme"]} 敞口 {pct:.0f}% 净值',
                           f'“{top["theme"]}”一个主题就占了 {pct:.0f}% 的经济敞口（{top["count"]} 只标的）。'
                           f'这不是分散，是同一个赌注押了 {top["count"]} 次；一次该主题的周期下行会同步击穿整篮子。'))
        elif pct >= 50:
            out.append(_fd('warn', f'主题偏重：{top["theme"]} 敞口 {pct:.0f}% 净值',
                           f'“{top["theme"]}”占 {pct:.0f}% 敞口，集中度偏高，注意与其余持仓的相关性。'))

    # 2. 总杠杆敞口
    exp_pct = total_exposure / net_liq * 100
    if exp_pct >= 120:
        out.append(_fd('crit', f'总敞口 {exp_pct:.0f}% > 净值',
                       f'含杠杆后经济敞口 ${total_exposure:,.0f} = 净值的 {exp_pct:.0f}%，你押的钱超过全部身家，'
                       f'下行会被放大。'))
    elif exp_pct >= 100:
        out.append(_fd('warn', f'总敞口 {exp_pct:.0f}% 略超净值', '含杠杆敞口已超 100% 净值，处于加杠杆状态。'))

    # 3. 杠杆 ETF 叠加
    if lev_positions:
        names = '、'.join(p['symbol'] for p in lev_positions)
        lev_mv = sum(p['market_value_usd'] for p in lev_positions)
        out.append(_fd('warn' if len(lev_positions) < 2 else 'crit',
                       f'{len(lev_positions)} 只杠杆 ETF：{names}',
                       f'每日重置杠杆 ETF（市值 ${lev_mv:,.0f}）有波动衰减 + 跳空风险，且吃掉最多维持保证金，'
                       f'不适合持有数日以上。想要敞口建议换对应正股无杠杆持有。'))

    # 4. 保证金缓冲
    if excess is not None:
        buf_pct = excess / net_liq * 100
        if buf_pct < 20:
            out.append(_fd('crit', f'保证金缓冲极薄：剩余流动性仅 ${excess:,.0f}（{buf_pct:.0f}% 净值）',
                           '剩余流动性归零即被强制平仓。当前缓冲很窄，一轮正常回撤就可能触发追缴。'))
        elif buf_pct < 35:
            out.append(_fd('warn', f'保证金缓冲偏薄：${excess:,.0f}（{buf_pct:.0f}% 净值）',
                           '缓冲不算充裕，且券商在急跌中会上调维持率，真实触发线更近。'))

    # 5. 现金为负（融资）
    if settled_cash is not None and settled_cash < 0:
        out.append(_fd('warn', f'已结算现金为负：${settled_cash:,.0f}（融资负债）',
                       '你在用借来的钱持仓，标的下跌时融资会先勒紧你，且有利息成本。'))

    # 6. 主题内重复标的（重叠）
    dom_names = [p['symbol'] for p in positions if p['theme'] == dominant]
    if len(dom_names) >= 3:
        out.append(_fd('warn', f'{dominant} 内重复押注：{len(dom_names)} 只（{"、".join(dom_names)}）',
                       'ETF 与成分股、正股与其杠杆版可能重复计数，实际集中度比表面更高。留最有把握的 1~2 只即可。'))

    if not out:
        out.append(_fd('good', '未发现显著结构性隐患', '当前集中度/杠杆/保证金缓冲在合理范围。'))
    return out


def _fd(severity, title, detail):
    return {'severity': severity, 'title': title, 'detail': detail}


def get_latest() -> dict | None:
    if STORE_PATH.exists():
        try:
            return json.loads(STORE_PATH.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None
