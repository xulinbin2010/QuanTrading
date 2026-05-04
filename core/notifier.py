"""
交易通知模块（可选）

支持 Gmail / 任意 SMTP 发送成交摘要邮件。
配置写入 .env：

  NOTIFY_EMAIL_TO=your@gmail.com
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=sender@gmail.com
  SMTP_PASS=gmail_app_password

若未配置 NOTIFY_EMAIL_TO，所有通知函数均为静默无操作。
Gmail App Password 获取：账户安全 → 两步验证 → 应用专用密码
"""
from __future__ import annotations
import os
import logging
import smtplib
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv
load_dotenv()

_logger = logging.getLogger(__name__)

_TO   = os.getenv('NOTIFY_EMAIL_TO', '').strip()
_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com').strip()
_PORT = int(os.getenv('SMTP_PORT', '587'))
_USER = os.getenv('SMTP_USER', '').strip()
_PASS = os.getenv('SMTP_PASS', '').strip()


def _enabled() -> bool:
    return bool(_TO and _USER and _PASS)


def _send(subject: str, body: str):
    if not _enabled():
        return
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = _USER
        msg['To']      = _TO
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(_HOST, _PORT, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(_USER, _PASS)
            s.sendmail(_USER, [_TO], msg.as_string())
        _logger.info(f'[Notifier] 邮件已发送：{subject}')
    except Exception as e:
        _logger.warning(f'[Notifier] 发送失败（不影响交易）：{e}')


# ── 公开接口 ──────────────────────────────────────────────────

def send_signal_summary(
    buy_list: list[dict],
    sell_list: list[dict],
    dry_run: bool,
    extra_info: str = '',
):
    """auto_trader.py 扫描信号后调用"""
    if not _enabled():
        return

    mode = '【dry-run 预览】' if dry_run else '【正式下单】'
    today = date.today().strftime('%Y-%m-%d')

    lines = [f'QuanTrading 信号摘要 {today} {mode}', '']

    if buy_list:
        lines.append(f'买入信号（{len(buy_list)} 只）：')
        for s in buy_list:
            sym   = s.get('symbol', s) if isinstance(s, dict) else s
            score = s.get('rs_score', '') if isinstance(s, dict) else ''
            lines.append(f'  + {sym}' + (f'  RS={score:.3f}' if score else ''))
    else:
        lines.append('无买入信号')

    lines.append('')

    if sell_list:
        lines.append(f'卖出/止损信号（{len(sell_list)} 只）：')
        for s in sell_list:
            sym    = s.get('symbol', s) if isinstance(s, dict) else s
            reason = s.get('reason', '') if isinstance(s, dict) else ''
            lines.append(f'  - {sym}' + (f'  [{reason}]' if reason else ''))
    else:
        lines.append('无卖出信号')

    if extra_info:
        lines += ['', extra_info]

    _send(f'[QT] 信号 {today} {mode}', '\n'.join(lines))


def send_fill_summary(
    filled: list[dict],
    cancelled: list[dict],
    unfilled: list[dict],
    target_date: str = '',
):
    """confirm_fills.py 成交确认后调用"""
    if not _enabled():
        return

    today = target_date or date.today().strftime('%Y-%m-%d')
    lines = [f'QuanTrading 成交确认 {today}', '']

    if filled:
        lines.append(f'✅ 成交（{len(filled)} 笔）：')
        for f in filled:
            sym   = f.get('symbol', '?')
            action= f.get('action', '')
            qty   = f.get('qty', '')
            price = f.get('filled_price', '')
            lines.append(f'  {action} {sym}  qty={qty}  @{price}')
    else:
        lines.append('无成交')

    lines.append('')

    if cancelled:
        lines.append(f'❌ 取消（{len(cancelled)} 笔）：' + ', '.join(
            f.get('symbol', '?') for f in cancelled))

    if unfilled:
        lines.append(f'⚠ 待定/未找到（{len(unfilled)} 笔）：' + ', '.join(
            f.get('symbol', '?') for f in unfilled))

    _send(f'[QT] 成交确认 {today}', '\n'.join(lines))
