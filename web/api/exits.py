"""半自动出场路由：待确认出场列表 / 人工决策（卖出|保留）/ 手动拉取 Claude 情报。

流程：auto_trader 触发 -15%/EMA21 后写 pending_exits（不下单），本路由供 Web UI：
  - 持仓页展示待确认卡片（触发原因 + Claude 情报）
  - 「确认卖出」→ 走 portfolio_svc.place_sell_order（盘中 MKT/DAY，盘外 LMT/OPG）
  - 「保留持仓」→ 标记 kept，当日不再重复提醒（次日条件仍成立会重新触发）
灾难硬止损（DISASTER_STOP_PCT）不经过本路由，auto_trader 直接自动卖出。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.database import Database

router = APIRouter(prefix='/api/exits', tags=['exits'])

_db = Database()
_db.connect()

_ET = ZoneInfo('America/New_York')


def _market_open_now() -> bool:
    et = datetime.now(_ET)
    if et.weekday() >= 5:
        return False
    t = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= t <= 16 * 60


@router.get('')
def list_exits():
    """待确认出场 + 最近已决策记录（审计用）。"""
    pending = _db.get_pending_exits('pending')
    all_rows = _db.get_pending_exits(None, limit=40)
    recent = [r for r in all_rows if r['status'] != 'pending'][:20]
    return {'pending': pending, 'recent': recent}


class DecideBody(BaseModel):
    action: str  # 'sell' | 'keep'


@router.post('/{pe_id}/decide')
def decide(pe_id: int, body: DecideBody):
    if body.action == 'keep':
        row = _db.decide_pending_exit(pe_id, 'kept')
        if row is None:
            raise HTTPException(status_code=404, detail='记录不存在或已被处理')
        return row

    if body.action != 'sell':
        raise HTTPException(status_code=400, detail="action 必须是 'sell' 或 'keep'")

    rows = [r for r in _db.get_pending_exits('pending') if r['id'] == pe_id]
    if not rows:
        raise HTTPException(status_code=404, detail='记录不存在或已被处理')
    row = rows[0]

    # 先真正下卖单，成功后才标记 sold；失败保持 pending 可重试。
    # 盘中：市价 DAY；盘前/盘后：限价 OPG（下限 = 触发价 × 0.95，与 auto_trader 对齐，
    # 防 IBKR 拒绝 MKT+OPG 组合）。
    from web.services import portfolio_svc
    try:
        if _market_open_now():
            result = portfolio_svc.place_sell_order(
                symbol=row['symbol'], qty=int(row['qty']), order_type='MKT', tif='DAY')
        else:
            floor = round(float(row['trigger_price'] or row['avg_cost']) * 0.95, 2)
            result = portfolio_svc.place_sell_order(
                symbol=row['symbol'], qty=int(row['qty']), order_type='LMT',
                limit_price=floor, tif='OPG')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f'下单失败（IB 未连接？）：{e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'下单失败：{e}')

    updated = _db.decide_pending_exit(pe_id, 'sold')
    return {'record': updated, 'order': result}


@router.post('/{pe_id}/intel')
def refresh_intel(pe_id: int):
    """手动（重新）拉取该记录的 Claude 出场情报。"""
    rows = [r for r in _db.get_pending_exits('pending') if r['id'] == pe_id]
    if not rows:
        raise HTTPException(status_code=404, detail='记录不存在或已被处理')
    row = rows[0]
    try:
        import json as _json
        from web.services.intel_svc import generate_exit_intel
        intel = generate_exit_intel([{
            'symbol': row['symbol'], 'avg_cost': row['avg_cost'],
            'cur_price': row['trigger_price'], 'ret': row['ret'] or 0.0,
            'reason': row['reason'],
        }])
        block = intel['per_symbol'].get(str(row['symbol']).upper()) or intel.get('raw') or ''
        if not block:
            raise HTTPException(status_code=500, detail='模型未返回该标的的情报内容，请重试')
        _db.set_pending_exit_intel(pe_id, _json.dumps(
            {'text': block, 'as_of': intel['as_of'], 'model': intel['model']},
            ensure_ascii=False))
        rows2 = [r for r in _db.get_pending_exits('pending') if r['id'] == pe_id]
        return rows2[0] if rows2 else {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        # MissingAPIKey 等配置类错误给 400，用户可读
        name = type(e).__name__
        code = 400 if name == 'MissingAPIKey' else 500
        raise HTTPException(status_code=code, detail=f'情报生成失败：{e}')
