"""收益对比 API"""
from fastapi import APIRouter, Query, HTTPException

router = APIRouter(prefix='/api/compare', tags=['compare'])


@router.get('')
def compare_returns(
    symbols: str = Query(..., description='逗号分隔的股票代码，如 NVDA,NVDL,SPY'),
    start: str = Query(..., description='开始日期，如 2023-01-01'),
    end: str | None = Query(None, description='结束日期，默认今天'),
):
    sym_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=400, detail='symbols 不能为空')
    if len(sym_list) > 8:
        raise HTTPException(status_code=400, detail='最多支持 8 只标的')

    from web.services.comparison_svc import get_comparison
    try:
        return get_comparison(sym_list, start, end)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
