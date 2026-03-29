# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 项目简介

基于 Interactive Brokers（IBKR）的个人量化交易平台，目标资金 ~$60K，策略为**日线波段交易**（非日内/高频）。
使用 yfinance 做历史回测，IB Gateway 做模拟/实盘执行。

**Python 版本：3.12**（venv 路径 `.venv/`）

---

## 安装依赖

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install ib_insync pymysql yfinance numpy pandas requests lxml html5lib fastapi "uvicorn[standard]" apscheduler
```

**注意：** 需手动创建 `.env` 文件（已加入 .gitignore），填写连接参数：

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=你的密码
DB_NAME=quantrading
IB_HOST=127.0.0.1
IB_PORT=4002    # 模拟盘 4002 / 实盘 4001
IB_CLIENT_ID=1
IB_TIMEOUT=60
```

策略/风控参数在 DB 中管理，通过 Web UI 的「系统配置」页修改，无需手动编辑文件。

---

## Web UI 启动

```bash
# 生产模式（推荐）：FastAPI 同时服务前端 + API，访问 http://localhost:3001
./start_web.sh

# 开发模式：API 热重载 + Vite 快速刷新
./start_web.sh --dev
# 开发时访问 http://localhost:5173（Vite，自动代理 /api → 3001）
```

**首次运行需构建前端：**
```bash
cd web/frontend && npm install && npm run build && cd ../..
```

**四个功能模块：**

| 模块 | URL | IB 依赖 | 说明 |
|------|-----|---------|------|
| 持仓总览 | `/#/` | 可选 | 余额/持仓需 IB，订单历史/净值曲线不需要 |
| 因子看板 | `/#/factors` | 否 | 全股票池因子扫描，缓存1小时，点行展开K线详情 |
| 策略回测 | `/#/backtest` | 否 | 参数化回测，异步执行，含净值曲线/交易明细 |
| 任务调度 | `/#/scheduler` | 否 | 管理定时任务，查看执行日志 |

**Web 模块目录结构：**
```
web/
  server.py           FastAPI 入口（端口 3001）
  api/                路由：portfolio / factors / backtest / scheduler
  services/           服务层：封装现有 Python 模块
  frontend/           React + TypeScript + Vite + ECharts + Tailwind
    src/pages/        四个页面组件
    dist/             生产构建产物（由 FastAPI 静态服务）
```

**调度器预设任务（默认关闭，需在 UI 中手动开启）：**

| Task ID | 说明 | Cron（UTC） |
|---------|------|-------------|
| `auto_trader` | OPG 下单 | `0 14 * * 1-5`（美东 9:00） |
| `confirm_fills` | 成交确认 | `35 14 * * 1-5`（美东 9:35） |
| `sp500_scanner` | 收盘扫描 | `0 22 * * 1-5`（美东 17:00） |
| `data_update` | 数据更新 | `0 23 * * 1-5`（美东 18:00） |

---

## 每日操作流程

```bash
# 北京时间 晚 9:00（美东 9:00 AM，开盘前30分钟）
python auto_trader.py --run              # 提交 OPG 单，提交完即可断开

# 北京时间 晚 9:35+（美东 9:35 AM，开盘后5分钟）
python confirm_fills.py                  # 查询成交回报，回写 MySQL，写日志
```

---

## 模块说明与独立运行命令

### 1. 选股模块（无需 IB，独立运行）

`sp500_scanner.py` — 每天收盘后扫描 RS 动量信号，输出买入候选、卖出报警、RS 排名。

```bash
python sp500_scanner.py                                          # 扫描 S&P 500，显示前15名
python sp500_scanner.py --top 20                                 # 显示前20名
python sp500_scanner.py --held NVDA AMD STX                      # 监控持仓卖出报警
python sp500_scanner.py --universe nasdaq100                     # 切换股票池
python sp500_scanner.py --extra TSLA PLTR --held NVDA            # 追加自选股
python sp500_scanner.py --universe nasdaq100 --top 10 --held NVDA AMD --extra COHR
```

---

### 2. 回测模块（无需 IB，独立运行）

`tests/backtest_rs.py` — 逐日模拟 RS 动量策略，输出完整业绩报告。

```bash
python -m tests.backtest_rs --period 3mo                         # 最近3个月（默认）
python -m tests.backtest_rs --start 2024-01-01 --end 2024-12-31 # 指定区间
python -m tests.backtest_rs --start 2025-10-01 --universe nasdaq100
python -m tests.backtest_rs --start 2025-10-01 --daily          # 打印每日持仓明细
python -m tests.backtest --symbol NVDA --fast 5 --slow 20        # MA 均线单股回测
```

---

### 3. 自动交易模块（需要 IB Gateway 运行）

`auto_trader.py` — 扫描信号 → 自动下单。

```bash
python auto_trader.py --run                              # 正式下单（盘前提交 OPG）
python auto_trader.py --run --held NVDA AMD STX          # 同时监控持仓止损/背离
python auto_trader.py --run --extra COHR TSM             # 追加非 S&P500 股票
python auto_trader.py --dry-run                          # 仅预览信号不下单
```

**OPG 限价保护：** 买入限价 = 昨收 × (1 + `MAX_ENTRY_SLIPPAGE`)，默认 1%。开盘跳空超过阈值则自动放弃，不追高。

**止损/卖出报警卖单：** 盘前（OPG）时改用限价卖出（止损下限 95%，报警下限 97%），防止 IBKR 拒绝 MKT+OPG 组合。

**现金等价 ETF（SGOV/BIL/USFR）：** 自动识别，市值计入现金，不占仓位槽，跳过止损和信号扫描。

---

### 4. 成交确认（需要 IB Gateway 运行）

`confirm_fills.py` — 开盘后查询 OPG 成交回报，回写 `orders` 表 `filled_price`，写日志。

```bash
python confirm_fills.py                      # 确认今日成交（9:35 AM ET 后运行）
python confirm_fills.py --date 2026-03-21    # 补确认历史某天
```

---

### 5. 交易终端（需要 IB Gateway 运行）

`main.py` — 交互式菜单，手动查价/下单/查账户。

```bash
python main.py
```

---

### 6. 历史数据拉取（需要 IB Gateway 运行）

`fetch_history.py` — 从 IBKR 拉取 K 线存入 MySQL。

```bash
python fetch_history.py --symbol NVDA AAPL --bar "1 day"
python fetch_history.py --update    # 仅增量更新已有数据
```

---

## 项目结构

```
# ── 入口脚本（根目录）────────────────────────────────────
sp500_scanner.py   # 【选股】RS 扫描器，独立运行，无需 IB
auto_trader.py     # 【执行】自动交易，需要 IB Gateway
confirm_fills.py   # 【确认】成交回报查询，9:35 AM ET 后运行
main.py            # 【终端】交互式手动交易终端，需要 IB Gateway
fetch_history.py   # 【数据】IBKR K 线拉取，需要 IB Gateway
config.py          # IB + MySQL 配置（git-ignored，需手动创建）

# ── 核心基础设施 ──────────────────────────────────────────
core/
  connection.py      # IB Gateway 连接 + 断线自动重连（MAX_RETRIES=20）
  trading.py         # 下单：market_buy/sell、limit_buy/sell，支持 tif=DAY/OPG
  account.py         # 账户余额 + 持仓查询，自动快照存库
  market_data.py     # 实时行情订阅 + 价格报警
  database.py        # MySQL：orders / account_snapshots / klines / signals 四张表
  historical_data.py # IBKR K 线拉取，支持增量更新
  universe.py        # 股票池：get_tickers(universe) 支持 sp500/nasdaq100/russell2000
  data_store.py      # Parquet 本地数据存储（替代 MySQL），回测/实盘共用
  logger.py          # 全局日志模块：logs/trading.log，每天切割，保留30天
  fmt.py             # 终端输出格式化工具（lj/rj 对齐函数）

# ── 策略层 ───────────────────────────────────────────────
strategies/
  base.py            # 抽象基类：generate_signals(df) → df（含 signal 列）
  rs_momentum.py     # 主策略：RS 动量 + 突破 + 放量 + 趋势过滤（见下方）
  ma_crossover.py    # 均线交叉策略（辅助/测试用）

# ── 回测层 ───────────────────────────────────────────────
tests/
  backtest_rs.py     # RS 动量投资组合回测：逐日模拟，含止损 + 每日持仓快照
  backtest.py        # MA 均线单股回测

# ── 日志 ─────────────────────────────────────────────────
logs/
  trading.log        # 运行日志（自动生成，每天切割）

# ── Web UI ───────────────────────────────────────────────
web/
  server.py          # FastAPI 入口，端口 3001
  api/               # 路由：portfolio / factors / backtest / scheduler
  services/          # 服务层：封装现有模块供 API 调用
  models.py          # Pydantic 请求/响应模型
  frontend/          # React + TS + Vite 前端
    src/pages/       # 持仓总览 / 因子看板 / 策略回测 / 任务调度
    dist/            # 生产构建（npm run build 生成，FastAPI 静态服务）
start_web.sh         # 一键启动脚本
```

---

## RS 动量策略买入/卖出逻辑

文件：`strategies/rs_momentum.py`

**买入信号（5个条件同时满足）：**

| 条件 | 参数 | 说明 |
|------|------|------|
| RS 跑赢 SPY | `rs_period=63` | 个股63日收益率 - SPY 63日收益率 > 0 |
| 价格突破 | `breakout_period=50` | 收盘价 > 前50日最高收盘价（shift(1) 排除当天） |
| 放量确认 | `vol_multiplier=1.5` | 当日成交量 > 20日均量 × 1.5 |
| 崩跌过滤 | `max_drawdown=-30%` | 距52周高点跌幅不超过30% |
| 趋势向上 | MA50 > MA200 | 黄金交叉过滤，减少熊市假突破 |

**卖出信号：** 价格创50日新高但成交量低于均量（量价背离，顶部信号）

**硬止损：** 跌破入场价 -15% 强制卖出

---

## 风控参数（$60K 资金配置）

所有参数统一定义在 `config.py`，`auto_trader.py` 和 `tests/backtest_rs.py` 均从中读取，**两处不要硬编码**：

| 参数 | 当前值 | 说明 |
|------|--------|------|
| `MAX_POSITIONS` | 5 | 最多同时持有5只 |
| `POSITION_PCT` | 0.15 | 每仓占净值15%（约$9,000/仓） |
| `CASH_RESERVE_PCT` | 0.25 | 永远保留25%现金（黑天鹅保护） |
| `STOP_LOSS_PCT` | -0.15 | 硬止损线 |
| `INITIAL_CASH` | 60,000 | 回测初始资金 |
| `MIN_CAP_B` | 10 | 最小市值 $10B（排除微盘股） |
| `MAX_CAP_B` | 500 | 最大市值 $500B（排除 mega-cap） |
| `DENY_INDUSTRIES` | `['Software—Application']` | 拒绝 SaaS 行业（模糊匹配） |

**`auto_trader.py` 内部常量（不在 config.py）：**

| 常量 | 值 | 说明 |
|------|----|------|
| `MAX_ENTRY_SLIPPAGE` | 0.01 | OPG 买入限价保护：最多接受昨收 +1% |
| `CASH_EQUIV` | `{'SGOV','BIL','USFR'}` | 现金等价 ETF 白名单 |
| `SELL_ON_ALERT` | True | 量价背离时是否自动卖出 |

---

## IB Gateway 配置

- 模拟盘端口：**4002**（IB Gateway）/ **7497**（TWS）
- 实盘端口：**4001** / **7496**
- 开启 API：Configuration → API → Settings → Enable ActiveX and Socket Clients
- 关闭只读模式才能下单

**订单类型自动切换（基于 `ZoneInfo('America/New_York')`，自动处理 EDT/EST）：**
- 美东 9:30-16:00 交易时段 → `DAY`（当日市价单）
- 盘前/盘后/周末 → `OPG`（下一个开盘集合竞价限价单）

**OPG 单不需要保持 Gateway 连接：** 提交后交易所自行撮合，9:35 再连接查询成交结果即可。

---

## 数据流

```
信号生成（全部 yfinance）：
  yfinance → DataStore（data/ Parquet 文件）→ RSMomentum.generate_signals() → 买卖信号

实盘执行：
  auto_trader.py → core/trading.py → IBKR 下单 → core/database.py（orders 表）
  confirm_fills.py → ib.trades() → orders 表更新 filled_price → logs/trading.log

回测路径：
  DataStore → RSMomentum → tests/backtest_rs.py 输出报告
```

---

## 已知待做事项（P1）

| 优先级 | 内容 | 文件 |
|--------|------|------|
| P1 | 行业集中度限制（`MAX_PER_SECTOR=2`） | `auto_trader.py`, `config.py` |
| P1 | SPY 熔断机制（近20日跌超8%暂停买入） | `auto_trader.py`, `config.py` |
| P2 | 回测加入 OPG 滑点保护（与实盘对齐） | `tests/backtest_rs.py` |
| P2 | scanner 加入市值/行业过滤 | `sp500_scanner.py` |

---

## 回测业绩参考

以下为含市值/行业过滤、T+1开盘成交（OPG，无前瞻偏差）的最新结果：

| 回测区间 | 股票池 | 收益率 | SPY | 超额 | Sharpe |
|----------|--------|--------|-----|------|--------|
| 2022 全年 | S&P 500 | -4.4% | -18.6% | +14.2% | -0.17 |
| 2023 全年 | S&P 500 | +18.3% | +26.7% | -8.4% | 0.88 |
| 2024 全年 | S&P 500 | +65.7% | +26.0% | +39.7% | 3.21 |
| 2025-10 至今 | S&P 500 | +17.7% | -2.4% | +20.1% | 1.17 |

**结论：S&P 500 为推荐股票池。** Russell 2000 小盘股趋势性差，假突破多，止损频繁。

> 前瞻偏差已修复：T日信号 → T+1 开盘价（OPG）成交，回测与实盘逻辑一致。
