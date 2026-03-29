# QuanTrading 进度记录

## 今天完成的

| # | 改进项 | 文件 |
|---|--------|------|
| ✅ #1 | 回测前瞻偏差修复：T日信号 → T+1开盘价成交（OPG） | `tests/backtest_rs.py` |
| ✅ #10 | 风控参数统一到 `config.py`，两个文件共用 | `config.py` |
| ✅ #5 | 重复下单防护：执行前查 `ib.openOrders()` | `auto_trader.py` |
| ✅ #8 | 信号持久化：每次扫描结果存入 MySQL `signals` 表 | `database.py`, `auto_trader.py` |
| ✅ 新 | 市值过滤：$10B ~ $500B（排除 mega-cap） | `config.py`, `auto_trader.py`, `backtest_rs.py` |
| ✅ 新 | 行业过滤：拒绝 SaaS（Software—Application） | `config.py`, `auto_trader.py`, `backtest_rs.py` |

## 当前回测结果（含过滤，T+1开盘成交）

| 年份 | 策略 | SPY | 超额 | Sharpe |
|------|------|-----|------|--------|
| 2022 | -4.4% | -18.6% | +14.2% | -0.17 |
| 2023 | +18.3% | +26.7% | -8.4% | 0.88 |
| 2024 | +65.7% | +26.0% | +39.7% | 3.21 |
| 2025-10至今 | +17.7% | -2.4% | +20.1% | 1.17 |

## 每天操作流程（已确认）

```bash
# 北京时间 晚 9:00 PM，开盘前 30 分钟跑一次
python auto_trader.py --run
# OPG 单提交到 IB Gateway，9:30 PM 自动成交，无需盯盘
```

---

## 明天待做（按优先级）

### P1 — 上模拟盘前必做

- [ ] **#3 订单成交确认**
  - 问题：下单后只等 1 秒，`filled_price` 永远是 NULL
  - 方案：监听 `ib.fillEvent`，成交后更新 MySQL `orders` 表的 `filled_price` 和 `status`
  - 文件：`core/trading.py`, `core/database.py`

- [ ] **#4 订单失败处理**
  - 问题：`trader.market_buy()` 失败时静默忽略
  - 方案：检查返回值，失败时打印告警
  - 文件：`auto_trader.py`（3处调用）

### P2 — 模拟盘运行中迭代

- [ ] **#7 结构化日志**
  - 问题：全部用 `print()`，运行完无法回溯
  - 方案：引入 `logging` 模块，输出到 `logs/trading_YYYYMMDD.log`
  - 涉及：所有文件

- [ ] **#9 通知机制**
  - 问题：止损/成交/断线无任何推送
  - 方案：Telegram Bot 或企业微信 webhook
  - 涉及：`auto_trader.py`（止损/买卖触发点）

- [ ] **#11 sp500_scanner.py 重构**
  - 问题：与 `auto_trader.py` 扫描逻辑重复，且还在用旧的 `yf.download`
  - 方案：scanner 复用 `scan_signals()`，只做展示层

### P3 — 实盘后再做

- [ ] **#6 限价单保护**（减少滑点，改用昨收 ±0.5% 限价 OPG）
- [ ] **#15 持仓对账**（IB实际持仓 vs orders表记录，差异告警）
- [ ] **#13 依赖版本锁定**（`pip freeze > requirements.txt`）

---

## 参数速查（config.py）

```python
MAX_POSITIONS    = 5       # 最多持仓只数
POSITION_PCT     = 0.15    # 单仓 15% 净值
CASH_RESERVE_PCT = 0.25    # 保留 25% 现金
STOP_LOSS_PCT    = -0.15   # 硬止损 -15%
INITIAL_CASH     = 60_000  # 回测初始资金

MIN_CAP_B        = 10      # 最小市值 $10B
MAX_CAP_B        = 500     # 最大市值 $500B
DENY_INDUSTRIES  = ['Software—Application']  # 拒绝 SaaS
```
