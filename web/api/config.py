"""系统配置 API 路由"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.database import Database
import config as cfg
import os
from dotenv import set_key, dotenv_values

router = APIRouter(prefix='/api/config', tags=['config'])

_db = Database()
_db.connect()

# 参数元数据：category + description
_META: dict[str, tuple[str, str]] = {
    'MAX_POSITIONS':           ('风控', '最多同时持有只数'),
    'POSITION_PCT':            ('风控', '每仓占净值比例'),
    'MAX_PER_SECTOR':          ('风控', '同行业最多持有只数'),
    'TARGET_RISK_PER_POS':     ('风控', '每仓目标风险比例（ATR止损）'),
    'STOP_LOSS_PCT':           ('止损', '硬止损触发线（触发后挂「待确认出场」，人工决定卖/留）'),
    'DISASTER_STOP_PCT':       ('止损', '灾难硬止损线（不可否决，触发即自动卖出兜底）'),
    'ATR_STOP_MULTIPLIER':     ('止损', 'ATR自适应止损倍数'),
    'ATR_STOP_FLOOR':          ('止损', 'ATR止损最大亏损下限'),
    'TRAIL_STOP_ACTIVATE_PCT': ('止损', '浮盈超过此值后启用移动止损'),
    'TRAIL_STOP_PCT':          ('止损', '从峰值回撤超过此值触发移动止损'),
    'TIME_STOP_DAYS':          ('止损', '时间止损观察期（交易日，0=禁用）'),
    'TIME_STOP_MIN_RETURN':    ('止损', '时间止损最低盈利门槛'),
    'SPY_BRAKE_PERIOD':        ('熔断', 'SPY 观察窗口（交易日数）'),
    'SPY_BRAKE_PCT':           ('熔断', 'SPY 跌超此幅度时暂停买入'),
    'VIX_BRAKE_LEVEL':         ('熔断', 'VIX 超过此值时暂停新建仓'),
    'BREADTH_MIN_PCT':         ('熔断', 'S&P500 站上 MA200 比例下限'),
    'BREADTH_MAX_POS':         ('熔断', '市场宽度不足时最多持仓数'),
    'INITIAL_CASH':            ('策略', '回测初始资金（实盘忽略）'),
    'VOL_SHRINK_RATIO':        ('策略', '量价背离判定：成交量低于均量×此值触发'),
    'MIN_CAP_B':               ('过滤', '最小市值（十亿美元）'),
    'MAX_CAP_B':               ('过滤', '最大市值（十亿美元）'),
    'FUND_FILTER_ENABLED':     ('过滤', '是否启用基本面硬门槛'),
    'FUND_MIN_ROE':            ('过滤', 'ROE 最低门槛'),
    'FUND_MAX_DE':             ('过滤', '负债权益比上限'),
    'FUND_MIN_REV_GROWTH':     ('过滤', '营收增长最低门槛'),
    'EARNINGS_AVOID_DAYS':     ('过滤', '财报前 N 日历日内不开新仓（0=禁用）'),
    'MAX_ENTRY_SLIPPAGE':      ('过滤', 'OPG 买入限价保护：最多接受昨收 +N%'),
    'INSIDER_DAYS':            ('内幕', '内幕买入观察窗口（天）'),
    'INSIDER_MIN_VALUE_K':     ('内幕', '内幕单笔最小金额（千美元）'),
}


@router.get('')
def get_config():
    """返回完整配置：策略参数 + 连接参数"""
    db_vals = {r[0]: r[1] for r in _db.get_all_config()}

    strategy = []
    for key, (default, typ) in cfg._DEFAULTS.items():
        category, description = _META.get(key, ('策略', ''))
        val = db_vals.get(key, str(default))
        strategy.append({
            'key':         key,
            'value':       val,
            'default':     default,
            'type':        typ.__name__,
            'category':    category,
            'description': description,
        })

    env = dotenv_values('.env')
    connection = [
        {'group': 'SQLite', 'key': 'DB_PATH',      'value': env.get('DB_PATH', cfg.DB_PATH),      'readonly': True},
        {'group': 'IB',     'key': 'IB_HOST',      'value': env.get('IB_HOST', str(cfg.IB_HOST)),      'readonly': False},
        {'group': 'IB',     'key': 'IB_PORT',      'value': env.get('IB_PORT', str(cfg.IB_PORT)),      'readonly': False},
        {'group': 'IB',     'key': 'IB_CLIENT_ID', 'value': env.get('IB_CLIENT_ID', str(cfg.IB_CLIENT_ID)), 'readonly': False},
        {'group': 'IB',     'key': 'IB_TIMEOUT',   'value': env.get('IB_TIMEOUT', str(cfg.IB_TIMEOUT)),   'readonly': False},
    ]

    return {'strategy': strategy, 'connection': connection}


class ParamUpdate(BaseModel):
    params: list[dict]


@router.put('')
def update_config(body: ParamUpdate):
    """批量更新策略参数，写入 DB 并刷新内存"""
    errors = []
    for item in body.params:
        key = item.get('key')
        value = item.get('value')
        if key not in cfg._DEFAULTS:
            errors.append(f'未知参数: {key}')
            continue
        _, typ = cfg._DEFAULTS[key]
        try:
            typ(value)
        except (ValueError, TypeError):
            errors.append(f'{key}: 期望 {typ.__name__}')
            continue
        category, description = _META.get(key, ('策略', ''))
        _db.set_config(key, str(value), typ.__name__, category, description)
    if errors:
        raise HTTPException(status_code=400, detail='; '.join(errors))
    cfg._init_globals()
    return {'status': 'ok', 'updated': len(body.params)}


@router.post('/reload')
def reload_config():
    """从 DB 重新加载参数到内存"""
    cfg._init_globals()
    return {'status': 'ok'}


class IBParams(BaseModel):
    IB_HOST:      str
    IB_PORT:      int
    IB_CLIENT_ID: int
    IB_TIMEOUT:   int


@router.put('/connection/ib')
def update_ib_connection(body: IBParams):
    """更新 IB 连接参数，写入 .env 文件"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env')
    set_key(env_path, 'IB_HOST',      body.IB_HOST)
    set_key(env_path, 'IB_PORT',      str(body.IB_PORT))
    set_key(env_path, 'IB_CLIENT_ID', str(body.IB_CLIENT_ID))
    set_key(env_path, 'IB_TIMEOUT',   str(body.IB_TIMEOUT))
    # 刷新内存
    cfg.IB_HOST      = body.IB_HOST
    cfg.IB_PORT      = body.IB_PORT
    cfg.IB_CLIENT_ID = body.IB_CLIENT_ID
    cfg.IB_TIMEOUT   = body.IB_TIMEOUT
    return {'status': 'ok'}
