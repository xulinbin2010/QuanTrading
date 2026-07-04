"""盘前简报路由：实时快照(快) + 持仓配置 + 调 Claude 生成完整简报(慢，含 web_search)。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.services import premarket_svc

router = APIRouter(prefix='/api/premarket', tags=['premarket'])


class HoldingRow(BaseModel):
    ticker: str = ''
    cost: str = ''
    weight: str = ''
    thesis: str = ''
    entry: str = ''
    stop: str = ''
    reason: str = ''
    trigger: str = ''


class ConfigBody(BaseModel):
    core: list[dict] = []
    swing: list[dict] = []
    watchlist: list[dict] = []


@router.get('/config')
def get_config():
    return premarket_svc.get_config()


@router.put('/config')
def put_config(body: ConfigBody):
    return premarket_svc.save_config(body.model_dump())


@router.get('/snapshot')
def snapshot():
    """实时宏观快照（无 LLM，秒回）。"""
    return premarket_svc.get_market_snapshot()


@router.get('/scan')
def scan():
    """盘前扫描：宏观快照 + 清单 + 各标的实时盘前报价（无 LLM）。"""
    return premarket_svc.get_scan()


@router.post('/briefing')
def briefing():
    """生成今日盘前简报（实时行情 + web_search + claude-opus-4-8，耗时较长）。"""
    try:
        return premarket_svc.generate_briefing()
    except premarket_svc.MissingAPIKey as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'生成失败：{e}')


@router.get('/core-cards')
def core_cards_cached():
    """读取核心票情报卡缓存（无 LLM，秒回；未生成过返回空对象）。"""
    from web.services import intel_svc
    return intel_svc.get_cached_core_cards() or {}


@router.post('/core-cards')
def core_cards_generate():
    """对 core 组每只票生成每日情报卡（web_search + claude-opus-4-8，耗时数分钟）。"""
    from web.services import intel_svc
    try:
        return intel_svc.generate_core_cards()
    except (premarket_svc.MissingAPIKey, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'生成失败：{e}')
