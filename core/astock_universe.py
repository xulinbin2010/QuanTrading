"""A 股股票池 / 申万行业 / 主题板块（akshare）。

- get_sw_l1_industries(): 申万一级行业 + 成分股（含权重，权重≈市值代理），缓存 1 天
- get_astock_names(codes): 代码→名称
- load_themes() / save_themes(): 自定义主题板块（data/astock_themes.json）

代理处理复用 astock_data_store._ensure_cn_direct（import 即生效）。
"""
from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path

# 触发国内域名直连（akshare 走代理会失败）
from core.astock_data_store import ak  # noqa: F401  （已在该模块内 _ensure_cn_direct）

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
_SW_CACHE      = ROOT / 'data' / '.astock_sw_cache.pkl'
_NAMES_CACHE   = ROOT / 'data' / '.astock_names_cache.pkl'
_THEMES_FILE   = ROOT / 'data' / 'astock_themes.json'
_SW_TTL_HOURS  = 24   # 行业成分变动慢，缓存 1 天
_NAMES_TTL_HOURS = 24  # 全市场代码→名称表缓存 1 天


# ── 申万一级行业 + 成分 ──────────────────────────────────────

def _load_sw_cache() -> dict | None:
    if not _SW_CACHE.exists():
        return None
    try:
        with open(_SW_CACHE, 'rb') as f:
            stored = pickle.load(f)
        if datetime.now() - stored.get('_time', datetime.min) < timedelta(hours=_SW_TTL_HOURS):
            return stored.get('data')
    except Exception:
        pass
    return None


def _save_sw_cache(data: dict):
    _SW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(_SW_CACHE, 'wb') as f:
        pickle.dump({'_time': datetime.now(), 'data': data}, f)


def get_sw_l1_industries(top_n: int | None = 40) -> dict:
    """返回申万一级行业及成分股。

    结构：
      { industry_name: {
          'code': '801010',
          'symbols': ['600519', ...],          # 按权重降序，最多 top_n 只
          'weights': {'600519': 2.43, ...},     # 最新权重（≈市值代理）
          'names': {'600519': '贵州茅台', ...},
      }, ... }

    top_n：每个行业只取权重最高的前 N 只（限制全市场扫描规模）；None=全部。
    带 24 小时本地缓存。
    """
    cached = _load_sw_cache()
    if cached is not None:
        return cached

    result: dict = {}
    try:
        sw = ak.sw_index_first_info()  # 列：行业代码/行业名称/成份个数/...
    except Exception as e:
        _logger.error(f'[AStockUniverse] 申万行业列表拉取失败：{e}')
        return {}

    for _, row in sw.iterrows():
        ind_code = str(row['行业代码']).split('.')[0]   # 801010.SI → 801010
        ind_name = str(row['行业名称'])
        try:
            cons = ak.index_component_sw(symbol=ind_code)  # 证券代码/证券名称/最新权重
        except Exception as e:
            _logger.warning(f'[AStockUniverse] 行业 {ind_name} 成分拉取失败：{e}')
            continue
        cons = cons.sort_values('最新权重', ascending=False)
        if top_n:
            cons = cons.head(top_n)
        symbols, weights, names = [], {}, {}
        for _, c in cons.iterrows():
            code = str(c['证券代码']).zfill(6)
            symbols.append(code)
            try:
                weights[code] = float(c['最新权重'])
            except Exception:
                weights[code] = 0.0
            names[code] = str(c['证券名称'])
        result[ind_name] = {
            'code': ind_code, 'symbols': symbols,
            'weights': weights, 'names': names,
        }

    if result:
        _save_sw_cache(result)
    return result


def _load_full_name_map() -> dict[str, str]:
    """全市场代码→名称权威表（akshare stock_info_a_code_name），缓存 1 天。"""
    if _NAMES_CACHE.exists():
        try:
            with open(_NAMES_CACHE, 'rb') as f:
                stored = pickle.load(f)
            if datetime.now() - stored.get('_time', datetime.min) < timedelta(hours=_NAMES_TTL_HOURS):
                return stored.get('data', {})
        except Exception:
            pass
    try:
        df = ak.stock_info_a_code_name()
        m = dict(zip(df['code'].astype(str).str.zfill(6), df['name'].astype(str)))
        _NAMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_NAMES_CACHE, 'wb') as f:
            pickle.dump({'_time': datetime.now(), 'data': m}, f)
        return m
    except Exception as e:
        _logger.warning(f'[AStockUniverse] 全市场名称表拉取失败：{e}')
        return {}


def get_astock_names(codes: list[str]) -> dict[str, str]:
    """代码→名称。先查申万缓存，缺失的再查全市场权威表，仍缺则返回代码本身。"""
    sw = _load_sw_cache() or {}
    name_map: dict[str, str] = {}
    for ind in sw.values():
        name_map.update(ind.get('names', {}))
    out = {c: name_map.get(c) for c in codes}
    missing = [c for c, v in out.items() if not v]
    if missing:
        full = _load_full_name_map()
        for c in missing:
            out[c] = full.get(c)
    return {c: (out[c] or c) for c in codes}


# ── 自定义主题板块 ──────────────────────────────────────────

# AI 硬件产业链精选股票池（材料级细分，约 120 只）。
# 运行时以 data/astock_themes.json 为准（可经 Web UI 增删）；此为版本控制的默认值。
_DEFAULT_THEMES = {
    'groups': {
        'glass_fiber': {'label': '玻纤/电子布', 'color': '#eab308', 'symbols': ['600176', '002080', '603256', '300395', '300196', '605006']},
        'ccl': {'label': '覆铜板CCL', 'color': '#f97316', 'symbols': ['600183', '002636', '688519', '000823']},
        'semi_material': {'label': '半导体材料', 'color': '#ca8a04', 'symbols': ['688126', '002409', '300054', '688019', '300346', '605358', '300398', '688268', '688550', '600206', '300666', '688234', '688146', '688401', '688233']},
        'semi_equip': {'label': '半导体设备', 'color': '#f59e0b', 'symbols': ['002371', '688012', '688072', '688082', '688120', '688037', '688361', '300604', '688200', '688409', '603061', '688652', '301369', '300567', '002975']},
        'chip_compute': {'label': '算力芯片/GPU/ASIC', 'color': '#ef4444', 'symbols': ['688256', '688041', '300474', '688047', '688521', '688385', '002049', '688107', '603893', '688099', '300672', '300458']},
        'analog_chip': {'label': '模拟/电源芯片', 'color': '#a855f7', 'symbols': ['300661', '688536', '688052', '688368', '688798', '688595', '603501', '688213', '300782', '603160', '688141', '688045']},
        'power_semi': {'label': '功率半导体/IGBT', 'color': '#d946ef', 'symbols': ['603290', '600460', '605111', '300373', '688187', '688711', '688396', '300623', '688172', '688261']},
        'storage': {'label': '存储芯片', 'color': '#06b6d4', 'symbols': ['603986', '300223', '688008', '301308', '001309', '688123', '688525', '688110', '688766']},
        'foundry': {'label': '晶圆制造代工', 'color': '#8b5cf6', 'symbols': ['688981', '688347', '600171', '688249', '688469']},
        'packaging': {'label': '封装测试', 'color': '#14b8a6', 'symbols': ['600584', '002156', '002185', '688362', '002079', '002436', '603005', '688372', '688352', '688403']},
        'passive': {'label': '被动元件MLCC', 'color': '#2dd4bf', 'symbols': ['300408', '000636', '002138', '002859', '603678', '002484', '600563', '300319', '603738']},
        'server': {'label': '服务器整机', 'color': '#3b82f6', 'symbols': ['000977', '601138', '603019', '000938', '000066', '002261', '000034', '000628']},
        'optical': {'label': '光模块/光器件', 'color': '#22c55e', 'symbols': ['300308', '300502', '300394', '002281', '688498', '300570', '300620', '688313', '603083', '688205', '301205', '300548', '000063']},
        'fiber_cable': {'label': '光纤光缆', 'color': '#16a34a', 'symbols': ['601869', '600487', '600522', '600498', '000070', '600105', '688143', '002491']},
        'pcb': {'label': 'PCB算力板', 'color': '#7c3aed', 'symbols': ['002463', '002916', '300476', '002384', '002938', '603228', '688183', '603920', '001389', '300903']},
        'connector': {'label': '铜连接/高速铜缆', 'color': '#ec4899', 'symbols': ['002475', '688629', '688668', '300679', '002130', '300563', '300913', '301328']},
        'cooling': {'label': '液冷/散热', 'color': '#fb923c', 'symbols': ['002837', '301018', '300499', '300602', '300684', '300990', '300249']},
        'power_supply': {'label': '电源/UPS配电', 'color': '#10b981', 'symbols': ['002851', '002335', '300870', '002518', '002364', '300693', '600405']},
        'idc': {'label': 'IDC运营', 'color': '#0ea5e9', 'symbols': ['300442', '300383', '300738', '603881', '300017']},
        'power_grid': {'label': '电力设备/特高压', 'color': '#84cc16', 'symbols': ['600406', '600089', '600312', '002028', '000400', '601179', '601126', '688676', '301291', '000682']},
    }
}


def load_themes() -> dict:
    if not _THEMES_FILE.exists():
        save_themes(_DEFAULT_THEMES)
        return _DEFAULT_THEMES
    with open(_THEMES_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_themes(data: dict):
    _THEMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_THEMES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 加股票 + 自动板块分类（申万三级行业反查）────────────────────

_SW3_CACHE = ROOT / 'data' / '.astock_sw3_cache.pkl'
_SW3_TTL_HOURS = 24

# 申万三级行业代码 → 主题板块 key（覆盖 AI 硬件相关三级行业）
SW3_TO_GROUP = {
    '850813': 'semi_material', '850818': 'semi_equip', '850816': 'foundry',
    '850817': 'packaging', '850815': 'analog_chip', '850814': 'chip_compute',
    '850812': 'power_semi', '850822': 'pcb', '850823': 'passive',
    '850833': 'optical', '857122': 'glass_fiber', '851025': 'fiber_cable',
    '851024': 'optical', '850703': 'server', '850715': 'cooling',
    '857336': 'power_supply', '857344': 'connector',
}
SW3_NAMES = {  # 仅展示用
    '850813': '半导体材料', '850818': '半导体设备', '850816': '集成电路制造',
    '850817': '集成电路封测', '850815': '模拟芯片设计', '850814': '数字芯片设计',
    '850812': '分立器件', '850822': '印制电路板', '850823': '被动元件',
    '850833': '光学元件', '857122': '玻纤制造', '851025': '通信线缆及配套',
    '851024': '通信网络设备及器件', '850703': '其他计算机设备',
    '850715': '制冷空调设备', '857336': '其他电源设备', '857344': '线缆部件及其他',
}

# 名称关键词 → 板块（高置信修正：申万会把这些细分归并到父类，名称命中时优先）
_NAME_KW_TO_GROUP = [
    ('覆铜', 'ccl'), ('存储', 'storage'), ('闪存', 'storage'), ('内存', 'storage'),
    ('光模', 'optical'), ('光纤', 'fiber_cable'), ('光缆', 'fiber_cable'),
    ('服务器', 'server'), ('液冷', 'cooling'), ('散热', 'cooling'),
]


def _build_sw3_reverse() -> dict:
    """构建 {code: {'group':key,'sw3':三级代码}} 反查表（科技三级成分），缓存 1 天。"""
    if _SW3_CACHE.exists():
        try:
            with open(_SW3_CACHE, 'rb') as f:
                st = pickle.load(f)
            if datetime.now() - st.get('_time', datetime.min) < timedelta(hours=_SW3_TTL_HOURS):
                return st.get('data', {})
        except Exception:
            pass
    rev: dict = {}
    for sw3, grp in SW3_TO_GROUP.items():
        try:
            cons = ak.index_component_sw(symbol=sw3)
            for _, c in cons.iterrows():
                code = str(c['证券代码']).zfill(6)
                rev.setdefault(code, {'group': grp, 'sw3': sw3})
        except Exception as e:
            _logger.warning(f'[AStockUniverse] 三级 {sw3} 成分拉取失败：{e}')
    if rev:
        _SW3_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SW3_CACHE, 'wb') as f:
            pickle.dump({'_time': datetime.now(), 'data': rev}, f)
    return rev


def classify_stock(code: str) -> dict:
    """给单只 A 股推荐所属板块。返回 {code,name,group,sw3_name,source}。group 可能为 None。"""
    code = str(code).zfill(6)
    name = get_astock_names([code]).get(code, code)
    for kw, grp in _NAME_KW_TO_GROUP:
        if kw in name:
            return {'code': code, 'name': name, 'group': grp, 'sw3_name': None, 'source': 'name'}
    info = _build_sw3_reverse().get(code)
    if info:
        return {'code': code, 'name': name, 'group': info['group'],
                'sw3_name': SW3_NAMES.get(info['sw3']), 'source': 'sw3'}
    return {'code': code, 'name': name, 'group': None, 'sw3_name': None, 'source': None}


def add_theme_stock(code: str, group: str) -> dict:
    """把股票加入指定主题板块（去重：若已在其它板块则先移除）。校验代码真实存在。"""
    code = str(code).zfill(6)
    full = _load_full_name_map()
    if full and code not in full:
        return {'ok': False, 'error': f'代码 {code} 不存在'}
    data = load_themes()
    groups = data.get('groups', {})
    if group not in groups:
        return {'ok': False, 'error': f'板块 {group} 不存在'}
    for gv in groups.values():
        syms = [str(s).zfill(6) for s in gv.get('symbols', [])]
        if code in syms:
            gv['symbols'] = [s for s in syms if s != code]
    groups[group]['symbols'] = [str(s).zfill(6) for s in groups[group].get('symbols', [])]
    groups[group]['symbols'].append(code)
    save_themes(data)
    return {'ok': True, 'code': code, 'group': group,
            'group_label': groups[group].get('label', group),
            'name': (full.get(code) if full else None) or get_astock_names([code]).get(code, code)}


def remove_theme_stock(code: str) -> dict:
    """从所有主题板块移除该股票。"""
    code = str(code).zfill(6)
    data = load_themes()
    groups = data.get('groups', {})
    removed = False
    for gv in groups.values():
        syms = [str(s).zfill(6) for s in gv.get('symbols', [])]
        if code in syms:
            gv['symbols'] = [s for s in syms if s != code]
            removed = True
    if removed:
        save_themes(data)
    return {'ok': removed, 'code': code}


def theme_group_of(code: str) -> tuple[str | None, str | None]:
    """该股所属主题板块 (key, label)；不在任何板块返回 (None, None)。"""
    code = str(code).zfill(6)
    for gk, gv in load_themes().get('groups', {}).items():
        if code in [str(s).zfill(6) for s in gv.get('symbols', [])]:
            return gk, gv.get('label')
    return None, None


def sw3_industry_of(code: str) -> str | None:
    """从已构建的申万三级反查缓存取该股的申万三级行业名（只读缓存，不触发网络构建）。"""
    code = str(code).zfill(6)
    if not _SW3_CACHE.exists():
        return None
    try:
        with open(_SW3_CACHE, 'rb') as f:
            st = pickle.load(f)
        info = st.get('data', {}).get(code)
        return SW3_NAMES.get(info['sw3']) if info else None
    except Exception:
        return None
