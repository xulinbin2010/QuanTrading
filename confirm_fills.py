"""
成交确认模块：查询今日 OPG 单成交情况，回写 MySQL，写日志。

执行时机：
  每天美东 9:35~10:00 之间运行一次（北京时间 晚 21:35~22:00）
  OPG 单在 9:30 开盘撮合，5分钟后基本全部有结果。

用法：
  python confirm_fills.py                      # 确认今日成交
  python confirm_fills.py --date 2026-03-21    # 补确认历史某天
  python confirm_fills.py --debug              # 打印 IB 原始数据用于排查
"""
import argparse
from datetime import date, datetime
from collections import defaultdict

from ib_insync import ExecutionFilter

from core.connection import IBConnection
from core.database  import Database
from core.trading   import Trading
from core.logger    import get_logger
import config

logger = get_logger('confirm_fills')


def _fetch_ib_data(ib, debug: bool):
    """
    从 IB Gateway 获取今日成交数据，返回两个 dict：
      fill_map:  orderId -> {'shares': float, 'avg_price': float}  (来自 reqExecutions)
      trade_map: orderId -> Trade                                   (来自 ib.trades() 备用)

    reqExecutions 是主要数据源：它显式向 IB 请求今日成交回报，
    不依赖当前 session 是否下过这些单，解决跨 session 成交查询问题。
    ib.trades() 作为备用，覆盖 reqExecutions 未包含的未完结状态（Cancelled 等）。
    """
    ib.sleep(5)   # 等待 IB Gateway 推送 openOrders 等初始数据

    # ── reqExecutions：今日所有成交回报（不限 session）────────────────
    fills = ib.reqExecutions(ExecutionFilter())
    ib.sleep(1)   # 给 IB 时间把 commissionReport 也推过来

    # 同一 orderId 可能有多笔部分成交，汇总成一条
    agg: dict[int, dict] = defaultdict(lambda: {'shares': 0.0, 'cost': 0.0, 'symbol': '', 'side': '', 'time': None})
    for f in fills:
        oid = f.execution.orderId
        agg[oid]['shares'] += f.execution.shares
        agg[oid]['cost']   += f.execution.shares * f.execution.price
        agg[oid]['symbol']  = f.contract.symbol
        agg[oid]['side']    = f.execution.side
        agg[oid]['time']    = f.execution.time

    fill_map = {}
    for oid, d in agg.items():
        fill_map[oid] = {
            'shares':    d['shares'],
            'avg_price': d['cost'] / d['shares'] if d['shares'] else 0,
            'symbol':    d['symbol'],
            'side':      d['side'],
            'time':      d['time'],
        }

    # ── ib.trades()：当前 session 能看到的订单状态（含 Cancelled）────
    trade_map = {t.order.orderId: t for t in ib.trades()}

    if debug:
        logger.info(f'[诊断] reqExecutions 返回 {len(fills)} 条执行回报，汇总 {len(fill_map)} 个 orderId:')
        for oid, d in fill_map.items():
            logger.info(f'  orderId={oid} [{d["symbol"]}] {d["side"]} '
                        f'{d["shares"]:.0f}股 avgPrice=${d["avg_price"]:.4f} time={d["time"]}')

        logger.info(f'[诊断] ib.trades() 返回 {len(trade_map)} 条:')
        for oid, t in trade_map.items():
            logger.info(f'  orderId={oid} [{t.contract.symbol}] {t.order.action} '
                        f'tif={t.order.tif} status={t.orderStatus.status} '
                        f'filled={t.orderStatus.filled} avgPrice={t.orderStatus.avgFillPrice}')

    return fill_map, trade_map


def run(trade_date: str, debug: bool = False):
    logger.info(f'===== 成交确认开始  日期：{trade_date} =====')

    # ── 查询当天待确认订单 ───────────────────────────────────
    db = Database()
    db.connect()

    # 清扫历史遗留：账龄 ≥3 天仍未对账的非终态单必然已死，标记 Expired，防止累积
    _stale = db.expire_stale_orders(days=3)
    if _stale:
        logger.warning(f'清扫 {_stale} 笔过期未对账订单（账龄≥3天）→ Expired')

    pending = db.get_pending_orders(trade_date)

    if not pending:
        logger.info('无待确认订单，退出')
        db.close()
        return

    logger.info(f'待确认订单：{len(pending)} 笔')
    if debug:
        for row in pending:
            db_id, symbol, action, order_type, qty, limit_price, order_id = row
            logger.info(f'  DB: id={db_id} [{symbol}] {action} order_id={order_id} '
                        f'limit_price={limit_price}')

    # ── 连接 IB Gateway，拉取今日成交 ────────────────────────
    conn = IBConnection()
    ib   = conn.connect()

    fill_map, trade_map = _fetch_ib_data(ib, debug)
    trader = Trading(ib, db=db)

    # ── 逐单比对，回写结果 ───────────────────────────────────
    filled_count    = 0
    partial_count   = 0
    cancelled_count = 0
    unfilled_count  = 0

    for row in pending:
        db_id, symbol, action, order_type, qty, limit_price, order_id = row
        logger.info(f'--- [{symbol}] {action} order_id={order_id}')

        # ① 优先用 reqExecutions（跨 session 可靠）
        fill = fill_map.get(order_id)
        if fill:
            filled_qty = fill['shares']
            remainder  = int(qty) - int(filled_qty)
            is_partial = remainder > 0 and int(filled_qty) > 0
            status     = 'PartialFill' if is_partial else 'Filled'

            db.update_order_fill(order_id, fill['avg_price'], filled_qty, status)
            logger.info(
                f'[{symbol}] {action} {int(filled_qty)}股 '
                f'成交价 ${fill["avg_price"]:.4f}  ✓ {status}'
            )
            filled_count += 1

            # 离线兜底：部分成交 → 补挂 DAY 限价单
            if is_partial and action == 'BUY':
                partial_count += 1
                day_limit = round(fill['avg_price'] * (1 + config.MAX_ENTRY_SLIPPAGE), 2)
                logger.warning(
                    f'[{symbol}] 部分成交 {int(filled_qty)}/{int(qty)} 股，'
                    f'补挂 DAY 限价买单 {remainder} 股 @ ${day_limit:.2f}'
                )
                trader.limit_buy(symbol, remainder, price=day_limit, tif='DAY')
            elif is_partial and action == 'SELL':
                partial_count += 1
                # 卖出部分成交 — 补挂 DAY 市价卖（确保剩余仓位能清掉）
                logger.warning(
                    f'[{symbol}] 卖出部分成交 {int(filled_qty)}/{int(qty)} 股，'
                    f'补挂 DAY 市价卖单 {remainder} 股'
                )
                trader.market_sell(symbol, remainder, tif='DAY')
            continue

        # ② 备用：ib.trades()（处理 Cancelled / PreSubmitted 等状态）
        trade = trade_map.get(order_id)
        if trade:
            status     = trade.orderStatus.status
            filled_qty = trade.orderStatus.filled
            avg_price  = trade.orderStatus.avgFillPrice

            if status == 'Filled':
                db.update_order_fill(order_id, avg_price, filled_qty, 'Filled')
                logger.info(
                    f'[{symbol}] {action} {int(filled_qty)}股 '
                    f'成交价 ${avg_price:.4f}  ✓ Filled (via trades)'
                )
                filled_count += 1

            elif status in ('Cancelled', 'ApiCancelled', 'Inactive'):
                db.update_order_fill(order_id, None, 0, status)
                suffix = ''
                if order_type == 'LMT' and limit_price:
                    suffix = f'  （开盘价高于限价 ${limit_price:.2f}，OPG 保护生效）'
                logger.warning(f'[{symbol}] {action} 未成交 — 状态：{status}{suffix}')
                cancelled_count += 1

            else:
                # Submitted / PreSubmitted：订单仍在途
                logger.warning(f'[{symbol}] {action} 状态仍为 {status}，稍后再确认')
                unfilled_count += 1
            continue

        # ③ 两个来源都找不到
        logger.warning(
            f'[{symbol}] order_id={order_id} 在 IB 中未找到。'
            f'可能原因：① 订单由其他 clientId 提交  ② 历史日期过久已清除  '
            f'③ 订单尚未被 IB Gateway 缓存（太早运行）'
        )
        unfilled_count += 1

    # ── 汇总 ────────────────────────────────────────────────
    logger.info(
        f'确认完毕 — 成交 {filled_count} 笔（其中部分成交补单 {partial_count} 笔）| '
        f'取消 {cancelled_count} 笔 | '
        f'待定/未找到 {unfilled_count} 笔'
    )
    logger.info('=' * 50)

    conn.disconnect()
    db.close()

    # 发送成交通知（若已配置 NOTIFY_EMAIL_TO）
    try:
        from core.notifier import send_fill_summary
        filled_rows    = [{'symbol': r[1], 'action': r[2], 'qty': r[3]} for r in pending[:filled_count]]
        cancelled_rows = []
        unfilled_rows  = []
        send_fill_summary(filled_rows, cancelled_rows, unfilled_rows, target_date=target_date)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description='IB 成交确认')
    parser.add_argument(
        '--date', default=date.today().strftime('%Y-%m-%d'),
        help='确认日期 YYYY-MM-DD（默认今天）'
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='打印 IB 原始数据，用于排查成交未识别问题'
    )
    args = parser.parse_args()
    run(args.date, debug=args.debug)


if __name__ == '__main__':
    main()
