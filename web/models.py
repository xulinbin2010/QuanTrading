"""Pydantic 请求/响应模型"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── 持仓总览 ───────────────────────────────────────────────

class BalanceResponse(BaseModel):
    net_liquidation: float
    total_cash: float
    unrealized_pnl: float
    realized_pnl: float
    buying_power: float


class PositionItem(BaseModel):
    symbol: str
    qty: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float


class OrderItem(BaseModel):
    id: int
    symbol: str
    action: str
    order_type: str
    quantity: float
    price: Optional[float]
    filled_price: Optional[float]
    status: str
    order_id: Optional[int]
    created_at: str


class AccountSnapshot(BaseModel):
    snapshot_at: str
    net_liquidation: float
    total_cash: float
    unrealized_pnl: float
    realized_pnl: float
    buying_power: float


class SignalItem(BaseModel):
    symbol: str
    rs_score: float
    close: float
    vol_ratio: float
    market_cap_b: Optional[float]
    industry: Optional[str]
    sector: Optional[str]
    insider_score: Optional[int]


class SignalsResponse(BaseModel):
    buy: list[SignalItem]
    sell: list[dict]
    spy_brake: bool


class IBStatusResponse(BaseModel):
    connected: bool
    account: Optional[str]


# ── 因子看板 ───────────────────────────────────────────────

class FactorRow(BaseModel):
    symbol: str
    close: float
    rs_score: float
    vol_ratio: float
    breakout: bool
    vol_surge: bool
    uptrend: bool
    not_crashed: bool
    signal: int      # 1=买, -1=卖, 0=持
    market_cap_b: Optional[float]
    industry: Optional[str]
    sector: Optional[str]
    # 基本面因子（按需启用，可为 None）
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    fcf_yield: Optional[float] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None


class StockFactorData(BaseModel):
    symbol: str
    ohlcv: list[dict]      # [{date, open, high, low, close, volume}]
    factors: list[dict]    # [{date, rs_score, breakout, vol_surge, uptrend, signal, ...}]


# ── 策略回测 ───────────────────────────────────────────────

class BacktestRequest(BaseModel):
    period: Optional[str] = '3mo'
    start: Optional[str] = None
    end: Optional[str] = None
    universe: str = 'sp500+ndx'  # 固定，不对外暴露选项
    top_n: int = 10
    min_cap_b: Optional[float] = None
    max_cap_b: Optional[float] = None
    deny_industries: Optional[list[str]] = None
    daily: bool = False
    factors: Optional[list[str]] = None           # 自定义因子列表（None = 使用默认 RSMomentum）
    factor_params: Optional[dict] = None          # 因子参数覆盖，如 {"rs_score": {"period": 126}}


class BacktestTaskResponse(BaseModel):
    task_id: str


class BacktestStatusResponse(BaseModel):
    task_id: str
    status: str       # pending / running / completed / failed
    progress: float   # 0.0 ~ 1.0
    error: Optional[str] = None


class BacktestSummary(BaseModel):
    initial_cash: float
    final_equity: float
    total_return: float
    annual_return: float
    spy_return: float
    excess_return: float
    max_drawdown: float
    sharpe: float
    total_trades: int
    win_rate: float
    total_commission: float


class TradeRecord(BaseModel):
    symbol: str
    entry_date: str
    exit_date: Optional[str]
    entry_price: float
    exit_price: Optional[float]
    shares: int
    pnl: Optional[float]
    ret: Optional[float]
    exit_reason: Optional[str]
    commission: float


class BacktestResult(BaseModel):
    task_id: str
    params: BacktestRequest
    summary: BacktestSummary
    equity_curve: list[dict]    # [{date, equity, spy_equity}]
    trades: list[TradeRecord]
    open_positions: list[dict]


class BacktestHistoryItem(BaseModel):
    task_id: str
    universe: str
    period: Optional[str]
    start: Optional[str]
    end: Optional[str]
    total_return: float
    sharpe: float
    created_at: str


# ── 任务调度 ───────────────────────────────────────────────

class TaskDefinition(BaseModel):
    task_id: str
    name: str
    command: str         # 相对于项目根目录的可执行脚本名
    args: list[str]      # 命令行参数列表
    cron_expr: str       # cron 表达式（5 段，UTC）
    enabled: bool = True


class TaskUpsertRequest(BaseModel):
    name: str
    command: str
    args: list[str] = []
    cron_expr: str
    enabled: bool = True


class TaskRunItem(BaseModel):
    id: int
    task_id: str
    task_name: str
    started_at: str
    finished_at: Optional[str]
    status: str          # running / success / failed
    exit_code: Optional[int]
    duration_s: Optional[float]
