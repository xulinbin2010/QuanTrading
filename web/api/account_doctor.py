"""账户诊断（桌面医生）路由：手输/粘贴 → 纯Python诊断。

不接 IB 实盘 API；数据靠用户手填或前端粘贴文本录入（不外传）。
诊断结果存本地 data/account_doctor.json。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.services import account_doctor_svc as svc

router = APIRouter(prefix='/api/account-doctor', tags=['account-doctor'])


class DiagnoseBody(BaseModel):
    account: dict = {}
    positions: list[dict] = []


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
