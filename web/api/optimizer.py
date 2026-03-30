"""因子组合优化器 API 路由"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from web.services import optimizer_svc

router = APIRouter(prefix='/api/optimizer', tags=['optimizer'])


@router.post('/run')
def run_optimizer(body: dict):
    """提交因子组合优化任务，返回 task_id"""
    try:
        task_id = optimizer_svc.submit_optimization(body)
        return {'task_id': task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/status/{task_id}')
def get_status(task_id: str):
    status = optimizer_svc.get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    return status


@router.get('/result/{task_id}')
def get_result(task_id: str):
    result = optimizer_svc.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail='结果尚未就绪')
    return result


@router.get('/history')
def get_history():
    return optimizer_svc.get_history()
