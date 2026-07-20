"""
维护清理：删除过期日志、调度执行记录，以及 30 天前的订单历史。

供调度任务 log_cleanup 调用，也可手动运行：
  python -m tools.clean_logs                 # 删除 3 天前日志（默认）
  python -m tools.clean_logs --days 7        # 改为 7 天
  python -m tools.clean_logs --dry-run       # 只列出将删除的文件，不实际删

安全约束（遵守 CLAUDE.md 数据安全）：
- 日志只动 logs/ 目录顶层文件，不递归、不碰行情 parquet/cache
- 永不删除正在写入的 trading.log（活跃日志，按名保护，与 mtime 无关）
- 跳过文件名含 'backup' 的文件（如 order_expire_backup_*.json 是订单数据备份，非日志）
- 逐个打印删除的文件名 + 大小，结果计入任务运行日志，可审计
- orders 仅删除 30 天前记录，且实删前写入 data/order_cleanup_backups/，可恢复
"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / 'logs'
ORDER_BACKUP_DIR = ROOT / 'data' / 'order_cleanup_backups'
ORDER_RETENTION_DAYS = 30

# 活跃日志：TimedRotatingFileHandler 正在写入的基文件，永不删除
PROTECTED = {'trading.log'}


def clean_logs(days: int = 3, dry_run: bool = False) -> dict:
    """删除 logs/ 下 mtime 早于 now-days 的顶层文件。返回统计 dict。"""
    if not LOG_DIR.exists():
        print(f'[clean_logs] 日志目录不存在：{LOG_DIR}')
        return {'deleted': 0, 'freed_bytes': 0, 'kept': 0}

    cutoff = time.time() - days * 86400
    deleted = freed = kept = 0
    print(f'[clean_logs] 目录 {LOG_DIR}  阈值：{days} 天前'
          f'（{time.strftime("%Y-%m-%d %H:%M", time.localtime(cutoff))} 之前）'
          f'{"  [DRY-RUN]" if dry_run else ""}')

    for p in sorted(LOG_DIR.iterdir()):
        if not p.is_file():
            continue
        # 活跃日志 + 数据备份（名字含 backup）一律保护，不随日志清理删除
        if p.name in PROTECTED or 'backup' in p.name.lower():
            kept += 1
            continue
        mtime = p.stat().st_mtime
        if mtime >= cutoff:
            kept += 1
            continue
        size = p.stat().st_size
        age_days = (time.time() - mtime) / 86400
        print(f'  {"将删" if dry_run else "删除"} {p.name:<40} '
              f'{size/1024:>8.1f} KB  {age_days:>5.1f} 天前')
        if not dry_run:
            try:
                p.unlink()
            except OSError as e:
                print(f'    [跳过] 删除失败：{e}')
                continue
        deleted += 1
        freed += size

    print(f'[clean_logs] {"将删除" if dry_run else "已删除"} {deleted} 个文件，'
          f'释放 {freed/1024/1024:.2f} MB；保留 {kept} 个（含活跃/未过期）')
    runs_deleted = _clean_task_runs(days=days, dry_run=dry_run)
    orders_deleted = _clean_orders(days=ORDER_RETENTION_DAYS, dry_run=dry_run)
    return {'deleted': deleted, 'freed_bytes': freed, 'kept': kept,
            'runs_deleted': runs_deleted, 'orders_deleted': orders_deleted}


def _clean_task_runs(days: int, dry_run: bool) -> int:
    """删除调度器「最近执行记录」(task_runs 表) 中超过 N 天的历史，保护 running 在途任务。
    DB 不可用（如无依赖环境）时静默跳过，不影响日志文件清理。"""
    try:
        from core.database import Database
    except Exception as e:
        print(f'[clean_logs] 跳过执行记录清理（DB 不可用）：{e}')
        return 0
    db = Database()
    db.connect()
    n = db.delete_old_task_runs(days=days, dry_run=dry_run)
    print(f'[clean_logs] 执行记录(task_runs)：{"将删除" if dry_run else "已删除"} '
          f'{n} 条（{days} 天前，保护 running）')
    return n


def _clean_orders(days: int = ORDER_RETENTION_DAYS, dry_run: bool = False) -> int:
    """清理超过保留期的 orders。

    实删前先把完整行写入 data/order_cleanup_backups/*.json，备份成功后才按
    本次预览到的 DB 主键精确删除；dry-run 只打印范围，不写备份、不删除。
    """
    try:
        from core.database import Database
    except Exception as e:
        print(f'[clean_logs] 跳过订单历史清理（DB 不可用）：{e}')
        return 0

    db = Database()
    db.connect()
    rows = db.get_old_orders(days=days)
    if not rows:
        print(f'[clean_logs] 订单历史(orders)：无 {days} 天前记录')
        return 0

    oldest = str(rows[0][9])[:19]
    newest = str(rows[-1][9])[:19]
    action = '将删除' if dry_run else '待备份并删除'
    print(f'[clean_logs] 订单历史(orders)：{action} {len(rows)} 条'
          f'（{oldest} ～ {newest}，保留最近 {days} 天）')
    if dry_run:
        for row in rows:
            print(f'  将删 order id={row[0]} {row[1]} {row[2]} {str(row[9])[:19]}')
        return len(rows)

    ORDER_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = ORDER_BACKUP_DIR / f'orders_before_{days}d_{stamp}.json'
    columns = ('id', 'symbol', 'action', 'order_type', 'quantity', 'price',
               'filled_price', 'status', 'order_id', 'created_at')
    payload = {
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'retention_days': days,
        'row_count': len(rows),
        'orders': [dict(zip(columns, row)) for row in rows],
    }
    with backup.open('x', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

    deleted = db.delete_orders_by_ids([row[0] for row in rows])
    print(f'[clean_logs] 订单历史(orders)：已删除 {deleted} 条；可恢复备份：{backup}')
    return deleted


def main():
    ap = argparse.ArgumentParser(description='清理过期日志、执行记录及 30 天前订单历史')
    ap.add_argument('--days', type=int, default=3, help='保留天数，超过则删除（默认 3）')
    ap.add_argument('--dry-run', action='store_true', help='只预览不删除')
    args = ap.parse_args()
    clean_logs(days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
