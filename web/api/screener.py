"""10x 候选筛选器 + 叙事错位观察名单 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from web.services import factor_svc

router = APIRouter(prefix='/api/screener', tags=['screener'])


@router.get('/tenbagger')
def tenbagger(force: bool = Query(False)):
    """Russell 2000 10x 候选筛选器（默认 7 天缓存，force=true 强制重算）"""
    try:
        return factor_svc.screen_tenbagger(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 叙事错位观察名单 ────────────────────────────────────────

class NarrativeEntryRequest(BaseModel):
    symbol: str
    old_category: Optional[str] = ''
    new_narrative: Optional[str] = ''
    thesis_notes: Optional[str] = ''
    target_price: Optional[float] = None


@router.get('/narrative')
def list_narrative():
    try:
        return factor_svc.list_narrative_watchlist()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/narrative')
def upsert_narrative(body: NarrativeEntryRequest):
    try:
        return factor_svc.upsert_narrative_entry(
            symbol=body.symbol,
            old_category=body.old_category or '',
            new_narrative=body.new_narrative or '',
            thesis_notes=body.thesis_notes or '',
            target_price=body.target_price,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/narrative/{entry_id}')
def delete_narrative(entry_id: int):
    if not factor_svc.delete_narrative_entry(entry_id):
        raise HTTPException(status_code=404, detail='记录不存在')
    return {'ok': True}
