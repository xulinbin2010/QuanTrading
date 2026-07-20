# QuanTrading

基于 Interactive Brokers（IBKR）的个人量化交易平台，目标资金 ~$60K，策略为**日线波段交易**（非日内/高频）。使用 yfinance 做历史回测，IB Gateway 做模拟/实盘执行，配套 Web UI 做监控与管理。

---

## 环境要求

- Python 3.12
- Node.js 18+（前端构建）
- IB Gateway 或 TWS（实盘/模拟盘执行，选股回测不需要）

---

## 安装

```bash
# 1. 创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖
pip install ib_insync yfinance numpy pandas pyarrow requests lxml html5lib \
            fastapi "uvicorn[standard]" apscheduler python-dotenv

# 3. 创建 .env 文件（已加入 .gitignore）
cat > .env << EOF
DB_PATH=data/quantrading.db
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=1
IB_TIMEOUT=60
EOF
```

> 数据库为 SQLite，路径由 `DB_PATH` 指定，首次运行自动创建。
> IB_PORT：模拟盘 `4002`，实盘 `4001`。

风控/策略参数通过 Web UI「系统配置」页管理，持久化到 DB `config_store` 表，无需手动编辑文件。

---

## Web UI 启动

```bash
# 首次运行需构建前端
cd web/frontend && npm install && npm run build && cd ../..

# 生产模式（推荐）：http://localhost:3001
./start_web.sh

# 开发模式：API 热重载 + Vite 快速刷新
./start_web.sh --dev
# 开发时访问 http://localhost:5178
```

### 功能页面

| 页面 | URL | 是否需要 IB | 说明 |
|------|-----|------------|------|
| 持仓总览 | `/#/` | 可选 | 余额/持仓/资产配比需 IB；净值曲线/订单历史不需要 |
| 因子看板 | `/#/factors` | 否 | 因子注册表管理：开启/关闭、调整参数 |
| 市场扫描 | `/#/scanner` | 否 | 全股票池因子扫描 + 内幕买入面板，点击个股展开详情 |
| 策略回测 | `/#/backtest` | 否 | 参数化回测，异步执行，含净值曲线/交易明细 |
| 任务调度 | `/#/scheduler` | 否 | 管理定时任务，查看执行日志 |
| 系统配置 | `/#/config` | 否 | 风控/策略参数实时修改 |

### 调度器预设任务

默认关闭，在 Web UI 中手动开启：

| Task ID | 说明 | Cron（北京时间） |
|---------|------|----------------|
| `auto_trader` | OPG 下单 | `0 22 * * 1-5` |
| `confirm_fills` | 成交确认 | `35 22 * * 1-5` |
| `sp500_scanner` | 收盘扫描 | `0 6 * * 2-6` |
| `data_update` | 数据更新 | `0 7 * * 2-6` |

---

## 每日操作流程

```bash
# 北京时间 22:00（美东 9:00 AM，开盘前30分钟）
python auto_trader.py --run          # 提交 OPG 单，提交完即可断开

# 北京时间 22:35（美东 9:35 AM，开盘后5分钟）
python confirm_fills.py              # 查询成交回报，回写 DB，写日志
```

---

## 模块独立运行

### 选股扫描（无需 IB）

```bash
python sp500_scanner.py                                 # 扫描 S&P 500，显示前15名
python sp500_scanner.py --top 20                        # 显示前20名
python sp500_scanner.py --held NVDA AMD STX             # 监控持仓卖出报警
python sp500_scanner.py --universe nasdaq100            # 切换股票池
python sp500_scanner.py --extra TSLA PLTR --held NVDA   # 追加自选股
```

### 回测（无需 IB）

```bash
python -m tests.backtest_rs --period 3mo                          # 最近3个月
python -m tests.backtest_rs --start 2024-01-01 --end 2024-12-31  # 指定区间
python -m tests.backtest_rs --start 2025-01-01 --daily           # 含每日持仓明细
python -m tests.backtest --symbol NVDA --fast 5 --slow 20         # MA 均线单股回测
```

### 自动交易（需要 IB Gateway）

```bash
python auto_trader.py --run                     # 正式下单
python auto_trader.py --run --held NVDA AMD     # 同时监控持仓止损
python auto_trader.py --dry-run                 # 仅预览信号，不下单
python auto_trader.py --dry-run --universe nasdaq100
```

OPG 限价保护：买入限价 = 昨收 × (1 + `MAX_ENTRY_SLIPPAGE`)，默认 1%，开盘跳空超阈值自动放弃。

### 成交确认（需要 IB Gateway）

```bash
python confirm_fills.py                         # 确认今日成交（9:35 AM ET 后运行）
python confirm_fills.py --date 2026-03-21       # 补确认历史某天
```

### 数据质量比对

```bash
python -m tools.compare_data --symbols AAPL NVDA MSFT --start 2024-01-01
python -m tools.compare_data --universe sp500 --sample 30 --start 2024-06-01
```

---

## IB Gateway 配置

| 模式 | IB Gateway 端口 | TWS 端口 |
|------|----------------|---------|
| 模拟盘 | 4002 | 7497 |
| 实盘 | 4001 | 7496 |

开启步骤：Configuration → API → Settings → **Enable ActiveX and Socket Clients**，并关闭只读模式。

订单类型自动切换：
- 美东 9:30–16:00 交易时段 → `DAY`
- 盘前/盘后/周末 → `OPG`（开盘集合竞价限价单）

---

## 策略说明

### RS 动量买入条件（5 个同时满足）

| 条件 | 参数 | 说明 |
|------|------|------|
| RS 跑赢 SPY | `rs_period=63` | 个股63日收益率超过 SPY |
| 价格突破 | `breakout_period=50` | 收盘价 > 前50日最高收盘价 |
| 放量确认 | `vol_multiplier=1.5` | 成交量 > 20日均量 × 1.5 |
| 崩跌过滤 | `max_drawdown=-30%` | 距52周高点跌幅不超过30% |
| 趋势向上 | MA50 > MA200 | 黄金交叉过滤 |/

**卖出信号：** 价格创50日新高但成交量萎缩（量价背离）。

### 多层止损体系

| 类型 | 参数 | 说明 |
|------|------|------|
| 硬止损 | `STOP_LOSS_PCT=-15%` | 跌破入场价强制卖出 |
| ATR 自适应 | `ATR_STOP_MULTIPLIER=2.5` | 入场价 - 2.5×ATR14，下限 -20% |
| 移动止损 | 浮盈 >8% 后启用 | 从峰值回撤 7% 触发 |
| 时间止损 | 20 交易日 | 持仓超时且未达 +5% 则卖出 |

### 仓位管理

| 参数 | 默认值 |
|------|--------|
| 最大持仓数 | 6 只 |
| 单仓占比 | 净值 15%（约 $9,000） |
| 同行业上限 | 3 只 |
| 最小市值 | $10B |

所有参数通过 Web UI 系统配置页实时修改，无需重启。

---

## 因子系统

因子注册表（`strategies/factors/registry.py`）共 20 个因子：

**技术因子（参与信号）：** `rs_score` / `breakout` / `volume_surge` / `volume_divergence` / `trend_filter` / `drawdown_filter` / `volatility_filter` / `momentum_quality` / `obv_trend` / `sector_rs` / `atr` / `volume_ma`

**基本面因子（仅展示）：** `revenue_growth` / `earnings_growth` / `roe` / `debt_to_equity` / `fcf_yield` / `pe_ratio` / `pb_ratio` / `earnings_avoid`

新因子工作流：注册表加入（默认关闭）→ 单股回测验证 → `RSMomentum(extra_filters=['factor_key'])` 推入生产。

---

## 项目结构

```
QuanTrading/
├── sp500_scanner.py        # 选股：RS 扫描器
├── auto_trader.py          # 执行：自动下单
├── confirm_fills.py        # 确认：成交回报
├── config.py               # 统一配置（.env + DB config_store）
├── start_web.sh            # Web UI 一键启动
│
├── core/                   # 核心基础设施
│   ├── connection.py       # IB Gateway 连接管理
│   ├── trading.py          # 下单封装
│   ├── account.py          # 账户余额 + 持仓
│   ├── universe.py         # 股票池（sp500 / nasdaq100 / russell2000）
│   ├── data_store.py       # Parquet 本地存储（yfinance，回测+选股共用）
│   ├── ibkr_data_store.py  # Parquet 本地存储（IBKR，数据验证用）
│   ├── stock_news.py       # 个股 SEC 公告 + 分析师数据 + 新闻
│   ├── earnings.py         # 财报日期查询
│   ├── insider.py          # OpenInsider 内幕买入数据
│   ├── database.py         # SQLite 操作
│   ├── historical_data.py  # IBKR K 线拉取
│   └── logger.py           # 日志（trading.log / server.log）
│
├── strategies/             # 策略层
│   ├── rs_momentum.py      # 主策略
│   ├── dynamic_factor.py   # Web 单股回测因子组合用
│   └── factors/            # 因子模块库（20 个因子）
│
├── tests/                  # 回测
│   ├── backtest_rs.py      # RS 动量组合回测
│   └── backtest.py         # MA 均线单股回测
│
├── tools/
│   └── compare_data.py     # yfinance vs IBKR 数据比对
│
├── web/                    # Web UI
│   ├── server.py           # FastAPI 入口（端口 3001）
│   ├── api/                # 路由
│   ├── services/           # 服务层
│   └── frontend/           # React + TypeScript + Vite + ECharts + Tailwind
│
├── data/
│   ├── stocks/             # yfinance Parquet（全股票池历史数据）
│   ├── stocks_ibkr/        # IBKR Parquet（验证用）
│   └── quantrading.db      # SQLite 数据库
│
└── logs/
    ├── trading.log         # 交易执行日志
    └── server.log          # Web 服务日志
```

---

## 数据流

```
选股信号：
  yfinance → DataStore（Parquet）→ RSMomentum → 买卖信号
  OpenInsider → 内幕买入参考
  SEC EDGAR  → 公告 / 分析师数据 → Web 个股详情

实盘执行：
  auto_trader → core/trading → IBKR → orders 表
  confirm_fills → ib.trades() → orders 表更新

Web UI：
  DataStore → factor_svc → 市场扫描 / 回测
  portfolio_svc → ib.reqHistoricalData() → 持仓实时报价
```

---

## 回测业绩

含市值/行业过滤，T+1 开盘价成交（OPG，无前瞻偏差）：

| 区间 | 股票池 | 收益率 | SPY | 超额 | Sharpe |
|------|--------|--------|-----|------|--------|
| 2022 全年 | S&P 500 | -4.4% | -18.6% | **+14.2%** | -0.17 |
| 2023 全年 | S&P 500 | +18.3% | +26.7% | -8.4% | 0.88 |
| 2024 全年 | S&P 500 | **+65.7%** | +26.0% | **+39.7%** | 3.21 |
| 2025-10 至今 | S&P 500 | +17.7% | -2.4% | **+20.1%** | 1.17 |

推荐股票池：**S&P 500**。Russell 2000 小盘股假突破多，止损频繁，回测表现明显弱于 S&P 500。

---

## 免责声明

本项目为个人量化研究工具，不构成投资建议。实盘交易存在亏损风险，请自行评估。
