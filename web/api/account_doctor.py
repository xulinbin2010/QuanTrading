"""账户诊断（桌面医生）路由：截图→Claude视觉解析(draft) + 纯Python诊断。

不接 IB 实盘 API；数据靠用户截图/手输喂入。截图解析会外发到 Anthropic API，
诊断结果存本地 data/account_doctor.json。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.services import account_doctor_svc as svc

router = APIRouter(prefix='/api/account-doctor', tags=['account-doctor'])


class ImageIn(BaseModel):
    media_type: str = 'image/png'
    data: str  # base64（不含 data:...; 前缀）


class ParseBody(BaseModel):
    images: list[ImageIn] = []


class DiagnoseBody(BaseModel):
    account: dict = {}
    positions: list[dict] = []


@router.post('/parse')
def parse(body: ParseBody):
    """截图 → Claude 视觉解析出持仓/保证金 draft（供前端表格预填、用户核对）。"""
    if not body.images:
        raise HTTPException(status_code=400, detail='未提供截图。')
    try:
        return svc.parse_screenshots([img.model_dump() for img in body.images])
    except svc.MissingAPIKey as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'解析失败：{e}')


@router.post('/diagnose')
def diagnose(body: DiagnoseBody):
    """用户核对后的持仓/保证金 → 确定性诊断（集中度/敞口/杠杆/压力测试/风险清单）。"""
    try:
        return svc.diagnose({'account': body.account, 'positions': body.positions})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'诊断失败：{e}')


@router.get('/latest')
def latest():
    """上次诊断结果（本地缓存）。"""
    return svc.get_latest() or {}
