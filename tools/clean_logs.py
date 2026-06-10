"""
日志清理：删除 logs/ 下修改时间超过 N 天的日志文件。

供调度任务 log_cleanup 调用，也可手动运行：
  python -m tools.clean_logs                 # 删除 3 天前日志（默认）
  python -m tools.clean_logs --days 7        # 改为 7 天
  python -m tools.clean_logs --dry-run       # 只列出将删除的文件，不实际删

安全约束（遵守 CLAUDE.md 数据安全）：
- 只动 logs/ 目录顶层文件，不递归、不碰其他目录
- 永不删除正在写入的 trading.log（活跃日志，按名保护，与 mtime 无关）
- 跳过文件名含 'backup' 的文件（如 order_expire_backup_*.json 是订单数据备份，非日志）
- 逐个打印删除的文件名 + 大小，结果计入任务运行日志，可审计
"""
import argparse
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / 'logs'

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
    return {'deleted': deleted, 'freed_bytes': freed, 'kept': kept}


def main():
    ap = argparse.ArgumentParser(description='删除 logs/ 下超过 N 天的日志文件')
    ap.add_argument('--days', type=int, default=3, help='保留天数，超过则删除（默认 3）')
    ap.add_argument('--dry-run', action='store_true', help='只预览不删除')
    args = ap.parse_args()
    clean_logs(days=args.days, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
