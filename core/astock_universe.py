"""A 股股票池 / 主题板块（akshare）；申万三级行业仅用于加股票时的板块反查。

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
_NAMES_CACHE   = ROOT / 'data' / '.astock_names_cache.pkl'
_THEMES_FILE   = ROOT / 'data' / 'astock_themes.json'
_NAMES_TTL_HOURS = 24  # 全市场代码→名称表缓存 1 天


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
    """代码→名称（全市场权威表，缺失则返回代码本身）。"""
    full = _load_full_name_map()
    return {c: (full.get(c) or c) for c in codes}


# ── 自定义主题板块 ──────────────────────────────────────────

# AI 硬件产业链精选股票池（材料级细分，约 120 只）。
# 运行时以 data/astock_themes.json 为准（可经 Web UI 增删）；此为版本控制的默认值。
_DEFAULT_THEMES = {
    'groups': {
        'glass_fiber': {'label': '玻纤/电子布', 'color': '#eab308', 'symbols': ['300196', '605006', '603256', '002080', '600176', '301526']},
        'ccl': {'label': '覆铜板CCL', 'color': '#f97316', 'symbols': ['600183', '002636', '688519', '000823', '603186', '300936']},
        'semi_substrate': {'label': '硅片衬底靶材', 'color': '#ca8a04', 'symbols': ['688234', '688126', '605358', '002129', '688233', '300666', '600206', '300706', '300263']},
        'semi_litho': {'label': '光刻胶掩膜', 'color': '#d97706', 'symbols': ['603650', '300346', '300236', '688401', '688138', '688721', '605588']},
        'semi_chem': {'label': '特气湿化学', 'color': '#b45309', 'symbols': ['688146', '688549', '600378', '688545', '603078', '603931', '688268', '688106', '600160']},
        'semi_equip_fab': {'label': '前道工艺设备', 'color': '#f59e0b', 'symbols': ['002371', '688012', '688072', '688037', '688082', '603690', '688120']},
        'semi_metrology': {'label': '量测检测(KLA系)', 'color': '#eab308', 'symbols': ['688361', '300567']},
        'semi_test': {'label': '后道测试(TER系)', 'color': '#f97316', 'symbols': ['300604', '688200', '603061', '301369']},
        'semi_parts': {'label': '设备零部件', 'color': '#fcd34d', 'symbols': ['688409', '688652']},
        'equip_offtopic': {'label': '非半导体主业(蹭)', 'color': '#94a3b8', 'symbols': ['002975', '002008', '301200', '300410', '300776']},
        'semi_equip_review': {'label': '设备·待核实', 'color': '#64748b', 'symbols': ['688808']},
        'chip_compute': {'label': '算力芯片/GPU/ASIC', 'color': '#ef4444', 'symbols': ['688256', '688041', '300474', '688047', '688521', '688385', '002049', '688107', '603893', '688099', '300672', '300458', '688795', '301269', '688608', '688018']},
        'analog_chip': {'label': '模拟/电源芯片', 'color': '#a855f7', 'symbols': ['300661', '688536', '688052', '688368', '688798', '688595', '603501', '688213', '300782', '603160', '688141', '688045', '688601', '688209', '688173']},
        'power_semi': {'label': '功率半导体/IGBT', 'color': '#d946ef', 'symbols': ['603290', '600460', '605111', '300373', '688187', '688711', '688396', '300623', '688172', '688261']},
        'storage': {'label': '存储芯片', 'color': '#06b6d4', 'symbols': ['603986', '300223', '688008', '301308', '001309', '688123', '688525', '688110', '688766']},
        'foundry': {'label': '晶圆制造代工', 'color': '#8b5cf6', 'symbols': ['688981', '688347', '600171', '688249', '688469']},
        'packaging': {'label': '封装测试', 'color': '#14b8a6', 'symbols': ['600584', '002156', '002185', '688362', '603005', '688372', '688352', '688403', '600667']},
        'passive': {'label': '被动元件MLCC', 'color': '#2dd4bf', 'symbols': ['002138', '002859', '603678', '002484', '600563', '603738', '603267', '301682', '600367', '301458', '000636', '300408', '300285', '605376']},
        'server': {'label': '服务器整机', 'color': '#3b82f6', 'symbols': ['000977', '601138', '603019', '000938', '000066', '002261', '000034', '000628', '603296']},
        'optical': {'label': '光模块/光器件', 'color': '#22c55e', 'symbols': ['300308', '300502', '300394', '002281', '300570', '300620', '603083', '688205', '301205', '300548', '000063', '000988', '688127', '002902']},
        'fiber_cable': {'label': '光纤光缆', 'color': '#16a34a', 'symbols': ['601869', '600487', '600522', '600498', '000070', '600105', '002491', '688143', '000586']},
        'pcb': {'label': 'PCB算力板', 'color': '#7c3aed', 'symbols': ['002384', '688183', '603920', '001389', '300903', '688813', '002815', '300657', '603936', '002217', '002913', '605258', '603328', '002463', '300476', '002938', '603228']},
        'connector': {'label': '铜连接/高速铜缆', 'color': '#ec4899', 'symbols': ['002475', '688629', '688668', '300679', '002130', '300563', '300913', '301328', '688800', '002897', '300351']},
        'cooling': {'label': '液冷/散热', 'color': '#fb923c', 'symbols': ['002837', '301018', '300499', '300602', '300684', '300990', '300249', '688600', '301128', '002126', '300547']},
        'power_supply': {'label': '电源/UPS配电', 'color': '#10b981', 'symbols': ['002335', '300870', '002518', '002364', '300693', '600405', '002851', '002580', '300593', '002922']},
        'idc': {'label': 'IDC运营', 'color': '#0ea5e9', 'symbols': ['300442', '300383', '300738', '603881', '300017', '600845', '603887', '603912', '300895']},
        'power_grid': {'label': '电力设备/特高压', 'color': '#84cc16', 'symbols': ['600406', '600089', '600312', '002028', '000400', '601179', '601126', '688676', '301291', '000682', '688303', '600353']},
        'copper_foil': {'label': '铜箔', 'color': '#b45309', 'symbols': ['688388', '002552', '002203', '688020', '301176', '301150', '301217', '301511', '301389', '600110']},
        'display_panel': {'label': '显示面板/OLED', 'color': '#a855f7', 'symbols': ['000725', '300162', '688550']},
        'ocs': {'label': '光交换/光路调度', 'color': '#0891b2', 'symbols': ['688195', '002222', '002273']},
        'optical_chip': {'label': '高端光芯片', 'color': '#15803d', 'symbols': ['688498', '688313', '688048']},
        'resin': {'label': '专用合成树脂', 'color': '#9333ea', 'symbols': ['300586', '300041', '300481', '300522', '300487', '601208', '605589', '603002']},
        'gas_turbine': {'label': '燃气轮机', 'color': '#dc2626', 'symbols': ['002353', '605060', '603308', '600875', '600482', '600893']},
        'power_compute': {'label': '算电协同', 'color': '#0d9488', 'symbols': ['001896', '002015', '301638', '000601', '688248', '600396']},
        'compute_lease': {'label': '算力租赁/调度', 'color': '#2563eb', 'symbols': ['603629', '300857', '301396', '688158', '300846', '688316', '300608', '603110']},
        'abf_substrate': {'label': 'ABF封装基板', 'color': '#fb923c', 'symbols': ['002916', '002436']},
        'silica_powder': {'label': '球形硅微粉', 'color': '#a3e635', 'symbols': ['688300', '002409', '301373', '688733']},
        'drill_bit': {'label': 'PCB钻针', 'color': '#94a3b8', 'symbols': ['301377', '000657', '301362', '688308']},
        'pcb_photoresist': {'label': 'PCB光刻胶', 'color': '#c084fc', 'symbols': ['300576', '002741', '300537', '300429']},
        'glass_substrate': {'label': '玻璃基板', 'color': '#38bdf8', 'symbols': ['603773', '920438', '600707', '600552', '301188']},
        'cmp': {'label': '抛光材料CMP', 'color': '#2dd4bf', 'symbols': ['688019', '300054']},
        'ceramic_substrate': {'label': '陶瓷基板', 'color': '#f472b6', 'symbols': ['003031', '301297', '300319']},
        'solder_paste': {'label': '锡焊膏', 'color': '#fbbf24', 'symbols': ['688379', '301319', '000960']},
        'emc_molding': {'label': '环氧塑封料EMC', 'color': '#a78bfa', 'symbols': ['688535', '300398']},
        'lead_frame': {'label': '引线框架', 'color': '#facc15', 'symbols': ['002119', '002079', '301678', '300283']},
        'quartz': {'label': '高纯石英材料', 'color': '#22d3ee', 'symbols': ['603688', '300395']},
        'diamond_cooling': {'label': '金刚石散热', 'color': '#e2e8f0', 'symbols': ['301071', '600172', '300179', '688028']},
    }
}

# ── 板块(17):细分小分类向上归并的中类,用于 A 股动能页的板块卡/板块强度/筛选/分层。
#    细分(astock_themes.json 的 50+ groups)仍是股票归属与"股票后面跟的分类标签"的来源;
#    板块只是聚合视图。新增细分 key 时记得在 SUBCAT_TO_BOARD 里补它归哪个板块。
BOARDS = {
    'semi_material':     {'label': '半导体材料',     'color': '#ca8a04'},
    'ccl_material':      {'label': '覆铜板CCL及材料', 'color': '#f97316'},
    'adv_substrate':     {'label': '先进封装/基板',   'color': '#7c3aed'},
    'semi_equip':        {'label': '半导体设备',     'color': '#f59e0b'},
    'chip_compute':      {'label': '算力/GPU芯片',   'color': '#ef4444'},
    'storage':           {'label': '存储芯片',       'color': '#06b6d4'},
    'analog_power_chip': {'label': '模拟/功率芯片',   'color': '#a855f7'},
    'foundry_pkg':       {'label': '晶圆制造/封测',   'color': '#8b5cf6'},
    'passive':           {'label': '被动元件MLCC',   'color': '#2dd4bf'},
    'pcb':               {'label': 'PCB算力板',      'color': '#6366f1'},
    'optical':           {'label': '光模块/光通信',   'color': '#22c55e'},
    'connector':         {'label': '铜连接/高速铜缆', 'color': '#ec4899'},
    'server':            {'label': '服务器整机',     'color': '#3b82f6'},
    'idc':               {'label': 'IDC/算力运营',   'color': '#0ea5e9'},
    'cooling':           {'label': '液冷/散热',      'color': '#fb923c'},
    'display_panel':     {'label': '显示面板/OLED',  'color': '#94a3b8'},
    'power':             {'label': '电力/配电/能源',  'color': '#84cc16'},
    'other':             {'label': '其他',          'color': '#64748b'},
}

# 细分小分类 key → 板块 key
SUBCAT_TO_BOARD = {
    'semi_substrate': 'semi_material', 'semi_litho': 'semi_material', 'semi_chem': 'semi_material',
    'silica_powder': 'semi_material', 'quartz': 'semi_material', 'cmp': 'semi_material',
    'ccl': 'ccl_material', 'glass_fiber': 'ccl_material', 'copper_foil': 'ccl_material',
    'resin': 'ccl_material', 'drill_bit': 'ccl_material', 'pcb_photoresist': 'ccl_material',
    'abf_substrate': 'adv_substrate', 'glass_substrate': 'adv_substrate', 'ceramic_substrate': 'adv_substrate',
    'lead_frame': 'adv_substrate', 'emc_molding': 'adv_substrate', 'solder_paste': 'adv_substrate',
    'semi_equip_fab': 'semi_equip', 'semi_metrology': 'semi_equip',
    'semi_test': 'semi_equip', 'semi_parts': 'semi_equip', 'cleanroom': 'semi_equip',
    'equip_offtopic': 'other', 'semi_equip_review': 'other',
    'chip_compute': 'chip_compute',
    'storage': 'storage',
    'analog_chip': 'analog_power_chip', 'power_semi': 'analog_power_chip',
    'foundry': 'foundry_pkg', 'packaging': 'foundry_pkg',
    'passive': 'passive',
    'pcb': 'pcb',
    'optical': 'optical', 'fiber_cable': 'optical', 'ocs': 'optical', 'optical_chip': 'optical',
    'connector': 'connector',
    'server': 'server',
    'idc': 'idc', 'compute_lease': 'idc',
    'cooling': 'cooling', 'diamond_cooling': 'cooling',
    'display_panel': 'display_panel',
    'power_supply': 'power', 'power_grid': 'power', 'gas_turbine': 'power', 'power_compute': 'power',
}


def board_of(subcat_key: str) -> str:
    """细分 key → 板块 key(未知细分归 'other')。"""
    return SUBCAT_TO_BOARD.get(subcat_key, 'other')




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
    '850813': 'semi_substrate', '850818': 'semi_equip', '850816': 'foundry',
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

# 名称关键词 → 板块（本地零网络，命中即归组；申万反查坏掉后这是主力分类手段）。
# 顺序敏感：更具体的词放前面，避免被宽泛词先截胡（如 '覆铜' 必须在 '铜' 之前，'光模' 在 '光' 之前）。
_NAME_KW_TO_GROUP = [
    # 覆铜板 / 铜箔 / 树脂（CCL 上游）
    ('覆铜', 'ccl'), ('铜箔', 'copper_foil'), ('树脂', 'resin'),
    # 玻纤 / 电子布
    ('玻纤', 'glass_fiber'), ('玻璃纤维', 'glass_fiber'), ('电子布', 'glass_fiber'),
    # 半导体材料三拆
    ('硅片', 'semi_substrate'), ('衬底', 'semi_substrate'), ('靶材', 'semi_substrate'),
    ('光刻', 'semi_litho'), ('掩膜', 'semi_litho'),
    ('特气', 'semi_chem'), ('电子特气', 'semi_chem'), ('特种气体', 'semi_chem'),
    ('湿电子', 'semi_chem'), ('电子化学', 'semi_chem'),
    # 芯片设计 / 制造 / 封测
    ('算力芯片', 'chip_compute'), ('模拟', 'analog_chip'),
    ('功率', 'power_semi'), ('碳化硅', 'power_semi'), ('分立器件', 'power_semi'),
    ('存储', 'storage'), ('闪存', 'storage'), ('内存', 'storage'),
    ('晶圆', 'foundry'), ('封测', 'packaging'), ('封装', 'packaging'),
    # 被动元件
    ('电容', 'passive'), ('电感', 'passive'), ('电阻', 'passive'),
    # 光通信
    ('光模', 'optical'), ('光器件', 'optical'),
    ('光纤', 'fiber_cable'), ('光缆', 'fiber_cable'),
    # PCB / 连接 / 散热 / 服务器
    ('线路板', 'pcb'), ('电路板', 'pcb'),
    ('连接器', 'connector'), ('铜缆', 'connector'), ('端子', 'connector'),
    ('服务器', 'server'), ('液冷', 'cooling'), ('散热', 'cooling'), ('制冷', 'cooling'),
    # 电源 / IDC / 电网
    ('电源', 'power_supply'), ('数据中心', 'idc'),
    ('特高压', 'power_grid'), ('输变电', 'power_grid'), ('电网', 'power_grid'),
    # 显示 / 燃机
    ('面板', 'display_panel'), ('显示', 'display_panel'),
    ('燃气轮机', 'gas_turbine'), ('燃机', 'gas_turbine'),
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


# 显式代码 → 板块覆盖表（最高优先级）：用于名字无行业线索、申万反查又拿不到的票。
# 维护方式：在 UI 手动归过组的票可顺手补到这里，下次自动识别即命中。
_CODE_TO_GROUP = {
    '301150': 'copper_foil',   # 中一科技：电解铜箔，名字无线索
}


def classify_stock(code: str) -> dict:
    """给单只 A 股推荐所属板块。返回 {code,name,group,sw3_name,source}。group 可能为 None。"""
    code = str(code).zfill(6)
    name = get_astock_names([code]).get(code, code)
    if code in _CODE_TO_GROUP:
        return {'code': code, 'name': name, 'group': _CODE_TO_GROUP[code], 'sw3_name': None, 'source': 'override'}
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
