"""社区热度采集（L1 结构化层）：ApeWisdom / Reddit 热帖 / StockTwits 情绪。

设计要点：
- 只采数字与标题样本，不做任何 LLM 调用（叙事解释在 intel_svc 按需触发，L2）
- 监控集（AI 池 + 持仓）由调用方传入 —— core 层不 import web 层
- 三源相互独立：单源失败只丢该源数据，不影响其余（免费 API 无 SLA，可插拔）
- 网络：requests 默认 trust_env=True 走系统代理（Reddit/StockTwits 需代理可达）；
  **不改全局 no_proxy / os.environ**，与 akshare 国内直连（A 股）共存
- ticker 识别防误报：优先认 $TICKER；裸词只认监控白名单内的全大写 token，
  易撞普通英文单词的代码（ARM/WOLF 等）必须带 $ 前缀才计数
"""
from __future__ import annotations

import re
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

_logger = logging.getLogger(__name__)
_ET = ZoneInfo('America/New_York')

_UA = {'User-Agent': 'Mozilla/5.0 (personal research; QuanTrading)'}
_TIMEOUT = 15

# 这些代码同时是常见英文单词，裸词匹配误报率高 → 只认 $TICKER 形式
_AMBIGUOUS = {'ARM', 'WOLF', 'FLEX', 'CARS', 'GOLD', 'REAL', 'OPEN', 'RUN',
              'ALL', 'ON', 'IT', 'ANY', 'NOW', 'PLAY', 'BIG', 'SEE', 'CEO'}

# Reddit 热帖抓取板块（覆盖散户大盘情绪 + 半导体垂直讨论）
REDDIT_SUBS = ('wallstreetbets', 'stocks', 'Semiconductors', 'hardware')


def et_trade_date() -> str:
    """美东日期（社区热度按美股交易日聚合）。"""
    return datetime.now(_ET).strftime('%Y-%m-%d')


# ── 源 1：ApeWisdom（Reddit 全站 ticker 提及聚合，现成排行榜）─────────────

def fetch_apewisdom(monitored: set[str], pages: int = 2) -> list[dict]:
    """拉全站提及榜前 pages×100 名，过滤到监控集。

    字段：mentions=24h 提及数，rank=全站排名，upvotes=相关帖子赞数。
    监控票不在榜内 = 热度低于前 200 截断线，服务层按 0 处理。
    """
    rows, td = [], et_trade_date()
    for page in range(1, pages + 1):
        try:
            r = requests.get(
                f'https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}',
                headers=_UA, timeout=_TIMEOUT)
            r.raise_for_status()
            results = r.json().get('results') or []
        except Exception as e:
            _logger.warning(f'[social] apewisdom page{page} 失败：{e}')
            break
        for it in results:
            sym = str(it.get('ticker') or '').upper()
            if sym not in monitored:
                continue
            def _i(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
            rows.append({
                'symbol': sym, 'source': 'apewisdom', 'trade_date': td,
                'mentions': _i(it.get('mentions')),
                'rank':     _i(it.get('rank')),
                'upvotes':  _i(it.get('upvotes')),
                'extra': {'mentions_24h_ago': _i(it.get('mentions_24h_ago')),
                          'rank_24h_ago':     _i(it.get('rank_24h_ago'))},
            })
    return rows


# ── 源 2：Reddit 热帖（标题级上下文，公开 JSON 端点）──────────────────────

def _match_tickers(text: str, monitored: set[str]) -> set[str]:
    """从帖子标题提取监控集内的 ticker。$X 直接认；裸词要求全大写 token 且不在歧义表。"""
    hits: set[str] = set()
    for m in re.findall(r'\$([A-Za-z]{1,6})\b', text):
        if m.upper() in monitored:
            hits.add(m.upper())
    for tok in re.findall(r'\b[A-Z]{2,6}\b', text):
        if tok in monitored and tok not in _AMBIGUOUS:
            hits.add(tok)
    return hits


def _reddit_token() -> str | None:
    """Reddit OAuth application-only token（免费）。

    匿名 JSON 端点已对数据中心/代理 IP 全面 403，必须走 OAuth：
    在 https://www.reddit.com/prefs/apps 创建 script 类型应用（免费、秒批），
    把 client_id / secret 写入 .env 的 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET。
    未配置返回 None，本源整体跳过（提及数主信号由 ApeWisdom 承担，不受影响）。
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass
    cid = os.environ.get('REDDIT_CLIENT_ID')
    sec = os.environ.get('REDDIT_CLIENT_SECRET')
    if not cid or not sec:
        return None
    try:
        r = requests.post(
            'https://www.reddit.com/api/v1/access_token',
            auth=(cid, sec), data={'grant_type': 'client_credentials'},
            headers=_UA, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json().get('access_token')
    except Exception as e:
        _logger.warning(f'[social] reddit OAuth 失败：{e}')
        return None


def fetch_reddit_posts(monitored: set[str],
                       subs: tuple[str, ...] = REDDIT_SUBS,
                       limit: int = 75) -> list[dict]:
    """扫各板块 hot 帖标题，统计监控票命中帖数/合计赞数，并留最热 3 条标题做展示样本。

    需 .env 配置 REDDIT_CLIENT_ID/SECRET（见 _reddit_token）；未配置时返回空（源级降级）。
    """
    token = _reddit_token()
    if not token:
        _logger.info('[social] 未配置 Reddit OAuth（REDDIT_CLIENT_ID/SECRET），跳过热帖标题源')
        return []
    headers = dict(_UA, Authorization=f'Bearer {token}')
    td = et_trade_date()
    agg: dict[str, dict] = {}
    for sub in subs:
        try:
            r = requests.get(
                f'https://oauth.reddit.com/r/{sub}/hot',
                params={'limit': limit}, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            children = (r.json().get('data') or {}).get('children') or []
        except Exception as e:
            _logger.warning(f'[social] reddit r/{sub} 失败：{e}')
            continue
        for ch in children:
            d = ch.get('data') or {}
            title = str(d.get('title') or '')
            score = int(d.get('score') or 0)
            for sym in _match_tickers(title, monitored):
                a = agg.setdefault(sym, {'posts': 0, 'score': 0, 'titles': []})
                a['posts'] += 1
                a['score'] += score
                a['titles'].append((score, f'r/{sub}: {title}'))
        time.sleep(0.5)   # 公开端点限速礼貌间隔
    return [{
        'symbol': sym, 'source': 'reddit_posts', 'trade_date': td,
        'mentions': a['posts'], 'upvotes': a['score'],
        'extra': {'titles': [t for _, t in sorted(a['titles'], reverse=True)[:3]]},
    } for sym, a in agg.items()]


# ── 源 3：StockTwits（逐票消息流，自带 Bullish/Bearish 标签）───────────────

def fetch_stocktwits(symbols: list[str],
                     max_symbols: int = 80,
                     pause: float = 0.4) -> list[dict]:
    """逐票拉最近 30 条消息，统计带情绪标签的多空数量 + 近 24h 消息数。

    未认证配额约 200 请求/小时/IP：单轮 cap 到 max_symbols，命中 429 立即停
    （symbols 按优先级传入——持仓在前，剩余配额给热度高的池内票）。
    """
    rows, td = [], et_trade_date()
    cutoff = datetime.now(_ET) - timedelta(hours=24)
    for sym in symbols[:max_symbols]:
        try:
            r = requests.get(
                f'https://api.stocktwits.com/api/2/streams/symbol/{sym}.json',
                headers=_UA, timeout=_TIMEOUT)
            if r.status_code == 429:
                _logger.warning(f'[social] stocktwits 触发限流（已采 {len(rows)} 只），本轮提前结束')
                break
            if r.status_code == 404:      # 该票无 stream（新股/冷门）
                continue
            r.raise_for_status()
            msgs = r.json().get('messages') or []
        except Exception as e:
            _logger.warning(f'[social] stocktwits {sym} 失败：{e}')
            continue
        bull = bear = recent = 0
        for m in msgs:
            senti = (((m.get('entities') or {}).get('sentiment') or {}).get('basic') or '')
            if senti == 'Bullish':
                bull += 1
            elif senti == 'Bearish':
                bear += 1
            try:
                created = datetime.strptime(m['created_at'], '%Y-%m-%dT%H:%M:%SZ')
                if created.replace(tzinfo=ZoneInfo('UTC')) >= cutoff:
                    recent += 1
            except (KeyError, ValueError):
                pass
        rows.append({
            'symbol': sym, 'source': 'stocktwits', 'trade_date': td,
            'mentions': recent, 'bull_cnt': bull, 'bear_cnt': bear,
            'extra': {'sampled': len(msgs)},
        })
        time.sleep(pause)
    return rows


# ── 采集编排 ─────────────────────────────────────────────────────────────

def collect(monitored: set[str], st_priority: list[str]) -> dict:
    """跑三源并写库。monitored=监控集（大写）；st_priority=StockTwits 采集顺序（持仓在前）。

    返回各源写入条数；任何源失败降级为 0 条，不抛异常。
    """
    from core.database import Database
    ape = fetch_apewisdom(monitored)
    red = fetch_reddit_posts(monitored)
    st  = fetch_stocktwits(st_priority)

    db = Database()
    db.connect()
    n = db.add_social_mentions(ape + red + st)
    pruned = db.prune_social_mentions(keep_days=90)
    db.close()
    _logger.info(f'[social] 采集完成：apewisdom {len(ape)} / reddit {len(red)} / '
                 f'stocktwits {len(st)}，入库 {n} 条，清理 {pruned} 条旧样本')
    return {'apewisdom': len(ape), 'reddit_posts': len(red),
            'stocktwits': len(st), 'saved': n}
