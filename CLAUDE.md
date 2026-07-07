# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Language

Always respond in Chinese (中文). Do not mix Korean or other languages into responses.

---

## Data Safety

- NEVER mass-delete parquet/cache files based on transient API failures (e.g., yfinance timeouts)
- Before blacklisting a ticker as delisted/invalid, verify against authoritative source (IVV holdings, NASDAQ listing) — do not rely on a single failed fetch
- Any denylist/filter that removes data must be reversible and logged
- Before any code that blacklists, filters out, or deletes tickers/data files: (1) show a dry-run list of what would be removed, (2) cross-check each against IVV holdings CSV or another authoritative source, (3) only proceed after user confirms. Never act on a single failed API call.

---

## Debugging Discipline

- Do not jump to conclusions on root cause; read the relevant code path first
- For IB/IBKR connection issues, check event loop context and reqAccountUpdates usage before hypothesizing about threading, pooling, or cooldowns
- When a fix doesn't work the first time, re-read the code rather than iterating on similar hypotheses
- Before proposing any fix for IB connection or async bugs: read the full call stack from entry point to failure, identify the exact event-loop/thread context at each await/sync boundary, and show the trace. Do not guess at threading or pooling causes.

---

## 前端共享组件规范

- **日期选择器**：统一使用 `web/frontend/src/components/DatePicker.tsx`，禁止用原生 `<input type="date">`。
  - 用法：`import DatePicker from '../components/DatePicker'`，props：`value: string`、`onChange: (v: string) => void`、`label?: string`
  - 工具函数 `dateToStr(d: Date): string` 和 `strToDate(s: string): Date | undefined` 也从该文件导出，可按需导入

- **股票代码显示（弹 K 线 modal）**：所有页面任何显示股票代码的位置都必须用 `<SymbolLink symbol="NVDA" />`，**不要**直接渲染 `{symbol}` 纯文本。
  - 用法：`import SymbolLink from '../components/SymbolLink'`
  - Props：`symbol: string`（必填）、`className?: string`（覆盖默认样式）、`children?: ReactNode`（自定义显示内容）、`title?: string`
  - 默认行为：点击 stopPropagation + 调用 `useStockChart().openChart(symbol)`，弹出全局共享 K 线 modal（含 MA10/20 + 成交量 + 因子 + 基本面 + 分析师 tab）
  - 与行级点击共存示例：表格行 `onClick` 仍可触发其他动作（如持仓详情），symbol 单独点会因 stopPropagation 只弹 K 线
  - 全局 modal 由 `<StockChartProvider>` 在 `App.tsx` 注入，无需在子组件再包一层
  - 不要再写本地 StockDetailPanel / K 线弹窗组件，统一复用 `components/StockChartModal.tsx`

- **页面底部「说明」文字字号**：每页底部那一段灰色说明/图例文字必须**清晰可读**，统一用 `text-sm`（14px）+ 颜色不低于 `text-slate-400`，容器间距用 `space-y-1`。
  - **禁止**用 `text-xs`（12px）或更小、以及 `text-slate-500/600` 这种过暗颜色做整段说明（用户反馈看不清）。
  - 适用范围：各页 `{/* 说明 */}` 注释下的 footer 说明块（AITracker / AStockTracker / Backtest / Comparison 等）。
  - 表格内单元格、角标、芯片（badge）等紧凑元素不受此限，可继续用 `text-xs` 及更小。

- **K 线图均线**：系统内所有 K 线图（美股 + A 股，含 StockChartModal / StockAnalysis / Portfolio）的均线统一使用 **EMA7 / EMA21**，禁止再用 MA10 / MA20。
  - 后端在 stock detail 的 `factors[].ma_fast`（=EMA7）/ `ma_slow`（=EMA21）字段输出，前端图例与线名显示为 `EMA7` / `EMA21`
  - 美股：`web/services/factor_svc.py::get_stock_factors`；A 股：`web/services/astock_momentum_svc.py::get_astock_detail`，均用 `close.ewm(span=N, adjust=False).mean()`
  - 注意：EMA 仅用于 K 线图展示；策略信号的 MA10>MA20 趋势过滤（`trend_filter` 因子）是独立逻辑，不要混用

---

## UI/Theme Conventions

- Match existing button styles (e.g., backtest button style) rather than introducing new colors like amber
- Keep theme options minimal — do not over-engineer with multiple theme variants unless explicitly requested
- When fixing theme colors, audit ALL color mappings (including blue-200, etc.) in one pass
- For any theme change: first grep the entire frontend for every color token and Tailwind class in use, produce a mapping table of old→new, and only then apply changes in one commit. Do not fix colors reactively as user spots them.

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
pip install ib_insync yfinance numpy pandas pyarrow requests lxml html5lib fastapi "uvicorn[standard]" apscheduler python-dotenv
```

**注意：** 需手动创建 `.env` 文件（已加入 .gitignore），填写连接参数：

```ini
DB_PATH=data/quantrading.db
IB_HOST=127.0.0.1
IB_PORT=4002    # 模拟盘 4002 / 实盘 4001
IB_CLIENT_ID=1
IB_TIMEOUT=60
```

策略/风控参数在 DB `config_store` 表中管理，通过 Web UI 的「系统配置」页修改，无需手动编辑文件。数据库为 SQLite，文件位于 `data/quantrading.db`，首次运行自动创建，无需额外安装。

---

## Web UI 启动

```bash
# 生产模式（推荐）：FastAPI 同时服务前端 + API，访问 http://localhost:3001
./start_web.sh

# 开发模式：API 热重载 + Vite 快速刷新
./start_web.sh --dev
# 开发时访问 http://localhost:5178（Vite，自动代理 /api → 3001）
```

**首次运行需构建前端：**
```bash
cd web/frontend && npm install && npm run build && cd ../..
```

**顶部导航栏（所有页面共享）：**
- ET 时钟（纽约时间实时显示）
- IB 账号状态：`● 实盘 · U12345678` / `● 模拟 · U12345678`，多账号时可下拉切换
- 主题切换：浅色 / 深色 / 跟随系统

**功能页面：**

| 模块 | URL | IB 依赖 | 说明 |
|------|-----|---------|------|
| 持仓总览 | `/#/` | 可选 | 余额/持仓/资产配比需 IB；订单历史/净值曲线不需要；支持 CSV 导出 |
| 市场扫描 | `/#/scanner` | 否 | 全股票池因子扫描 + 内幕买入面板，缓存1小时，点行展开K线详情 |
| 因子优化 | `/#/optimizer` | 否 | 穷举因子组合 × 参数网格，按 Sharpe 排名，含预计算加速 |
| 策略回测 | `/#/backtest` | 否 | 4 tab：策略回测 / 单股回测 / 收益对比 / **A 股动能轮动**(每周一 rebalance,4 个策略可选) |
| 美股AI追踪 | `/#/ai` | 否 | 3 tab：产业图谱(universe 策展,GPU/网络/电力) / 动能轮动 / **财报对比**(最多3只横向比营收/净利/EPS);成员自动获得 `auto_trader` 优先池待遇 |
| A股AI追踪 | `/#/astock` | 否 | A 股动能扫描(主题板块/申万行业),188 只 AI 硬件,含板块强度排名 |
| 任务调度 | `/#/scheduler` | 否 | 管理定时任务，查看执行日志 |
| 系统配置 | `/#/config` | 否 | 风控/策略参数实时修改，持久化到 DB |

**持仓总览页关键实现细节：**
- 资产配比以 `net_liquidation`（IB实时净值）为分母，百分比始终准确
- 期权垂直价差自动识别：数量匹配（+N/-N）为一组，显示为 `GOOGL C325/340`；余下单腿单独显示
- 价格获取用 `reqHistoricalData`（不依赖 Level 1 行情订阅），盘中/盘后均可用
- ib_insync 在 FastAPI AnyIO 线程中通过 `ThreadPoolExecutor` + 固定 event loop 调用

**策略回测页「单股回测」tab 关键实现：**
- 后端 `strategies/ema_pullback.py::run_ema_pullback_backtest` → `single_backtest_svc`（异步）→ `POST /api/single-bt/run`，T 日触发 / T+1 开盘成交
- 一次回测跑多策略对照：`ema_pullback`(EMA21 补仓) / `retrace_add`(逢跌加仓) / `rs_only`(RSMomentum) / `buy_hold`(满仓 B&H) / `buy_hold_base`(同底仓 B&H) / SPY
- **`retrace_add`（逢跌加仓）— 单边强趋势股「增厚 B&H」对照策略**：满仓底仓 + 从滚动峰值回撤分档加仓(默认 -12%/-20%/-28% 各 0.4x) + **只买不卖**(退出靠人工看基本面，无自动止损)。加仓走 margin，总杠杆上限 `retrace_max_leverage`(默认 2.0)，可选 `retrace_rsi_boost`(回撤档触发时 RSI<40 加码×1.5)。`_simulate_retrace_add` 实现，每档整段回测仅触发一次
  - **设计依据**：纯单边牛股(如 MU YTD +260%)上，主动减仓/止损/高抛都是负 alpha(卖了买不回)，唯一有正期望的增厚动作是「逢大跌加仓、从不高抛」，回测可超满仓 B&H，代价是杠杆放大回撤
  - 回撤按**滚动峰值**算(非相对成本)，故加仓价可能高于底仓价(上升途中回调照买)；前端可调档位/杠杆/RSI 加码，权益曲线金色线 + 「加仓明细」表
  - 反面教训：EMA21 补仓默认 `base_pct=0.5` + EMA50 硬止损，在单边股上必跑输 B&H(底仓踏空 + 止损被甩出后高位追回)；想贴近 B&H 要调高 `base_pct`、放宽或禁用止损

**美股AI追踪页「财报对比」tab 关键实现：**
- 后端 `ai_momentum_svc.get_earnings_compare(symbols)` → `GET /api/ai/earnings-compare`，最多 3 只
- 数据：快照(营收/盈利 YoY、PE/PS/毛利/市值，复用 `get_stock_info`) + 最近 5 季营收/净利/EPS(yfinance `quarterly_income_stmt`，pickle 缓存 24h `data/.earnings_compare_cache.pkl`)
- 三列独立卡片(各公司营收/净利柱状 + EPS 行) + 下方三股合并图(营收/净利各一张折线，蓝/绿/橙固定配色)
- **财季不对齐**：各公司财年截止月不同(MU 8 月底)，合并图横轴取真实财季日期(`2025.7` 格式)并集排序，缺失点 `connectNulls` 连，**不按日历强行对齐**；以趋势 + YoY 增速为主轴
- 合并图「绝对值/增长指数(首季=100)」切换：绝对值下营收用对数轴(大小盘同框可见)；指数模式比增长斜率，净利负/零基准无法指数化显示为空
- yfinance 季度数据可能混入 TTM/重述值(单季异常跳变)，前端标注「异常以官方财报为准」，不做静默修正

**Web 模块目录结构：**
```
web/
  server.py           FastAPI 入口（端口 3001）
  api/                路由：portfolio / factors / backtest / scheduler / config / optimizer
  services/           服务层：封装现有 Python 模块
    factor_svc.py     因子扫描、内幕数据、K线详情因子/基本面
    backtest_svc.py   异步回测执行
    optimizer_svc.py  因子组合优化（含预计算缓存）
    performance_svc.py 净值/业绩指标计算
    portfolio_svc.py  持仓/账户数据
    scheduler_svc.py  APScheduler 任务管理
  models.py           Pydantic 请求/响应模型
  frontend/           React + TypeScript + Vite + ECharts + Tailwind
    src/pages/        各功能页面组件（Portfolio/MarketScan/Backtest/AITracker/AStockTracker/Optimizer/Scheduler/Config 等）
    dist/             生产构建产物（由 FastAPI 静态服务）
start_web.sh          一键启动脚本
```

**调度器预设任务（默认关闭，需在 UI 中手动开启）：**

| Task ID | 说明 | Cron（北京时间） |
|---------|------|----------------|
| `auto_trader` | OPG 下单 | `0 22 * * 1-5`（北京 22:00） |
| `confirm_fills` | 成交确认 | `35 22 * * 1-5`（北京 22:35） |
| `sp500_scanner` | 收盘扫描 | `0 6 * * 2-6`（北京周二至六 06:00） |
| `data_update` | 数据更新 | `0 7 * * 2-6`（北京周二至六 07:00） |

---

## 每日操作流程

```bash
# 北京时间 晚 22:00（美东 9:00 AM，开盘前30分钟）
python auto_trader.py --run              # 提交 OPG 单，提交完即可断开

# 北京时间 晚 22:35（美东 9:35 AM，开盘后5分钟）
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
python sp500_scanner.py --universe nasdaq100 --top 10 --held NVDA AMD --extra MSFT
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
python auto_trader.py --run --extra MSFT TSM             # 追加非 S&P500 股票
python auto_trader.py --dry-run                          # 仅预览信号不下单
python auto_trader.py --dry-run --universe nasdaq100     # 切换股票池
```

**OPG 限价保护：** 买入限价 = 昨收 × (1 + `MAX_ENTRY_SLIPPAGE`)，默认 1%。开盘跳空超阈值则自动放弃，不追高。

**止损/卖出报警卖单：** 盘前（OPG）改用限价卖出，防止 IBKR 拒绝 MKT+OPG 组合。

**现金等价 ETF（SGOV/BIL/USFR）：** 自动识别，市值计入现金，不占仓位槽，跳过止损和信号扫描。

**待成交市价卖单感知（含 Web UI 手动卖出）：** auto_trader 执行前从 DB 读当日非终态卖单，对**市价单(MKT/MOC)且卖出股数≥持仓**的标的，视为退出 → 腾出仓位槽 + 预计回笼资金计入可用现金（处理同现金等价 ETF：从 stock_positions 移出，不被止损/报警重复处理，买入循环不回补）。这样盘前手动卖出后，**同一轮 auto_trader 就能补仓**，不必等卖单实际成交（市价单开盘集合竞价必成交）。限价卖单不计入（可能不成交，防过投）；仅认全平、部分卖出不腾槽；卖单若已成交则持仓不在 stock_positions，自校正不误腾。注意：Web UI 用不同 clientId 下单，`ib.openTrades()` 跨 client 不可见，故走 DB 而非 IB 查询。

**`auto_trader.py` 内部常量（不在 config.py）：**

| 常量 | 值 | 说明 |
|------|----|------|
| `CASH_EQUIV` | `{'SGOV','BIL','USFR'}` | 现金等价 ETF 白名单 |
| `SELL_ON_ALERT` | False | 已废弃：量价背离自动卖出已下线，恒 False（实盘出场见「止损体系（半自动版）」） |

---

### 4. 成交确认（需要 IB Gateway 运行）

`confirm_fills.py` — 开盘后查询 OPG 成交回报，回写 `orders` 表 `filled_price`，写日志。

```bash
python confirm_fills.py                      # 确认今日成交（9:35 AM ET 后运行）
python confirm_fills.py --date 2026-03-21    # 补确认历史某天
```

---

## 项目结构

```
# ── 入口脚本（根目录）────────────────────────────────────
sp500_scanner.py   # 【选股】RS 扫描器，独立运行，无需 IB
auto_trader.py     # 【执行】自动交易，需要 IB Gateway
confirm_fills.py   # 【确认】成交回报查询，9:35 AM ET 后运行
                   #   历史遗留非终态订单逐笔与 IB 对账（已成交→回填 / 僵尸挂单→撤销 / 无踪迹→Expired），
                   #   不再盲目按账龄标 Expired（防已成交漏记 + 休市日顺延僵尸单）
config.py          # 统一配置：连接参数从 .env 读，风控参数从 DB config_store 读

# ── 核心基础设施 ──────────────────────────────────────────
core/
  connection.py      # IB Gateway 连接 + 断线自动重连（MAX_RETRIES=20）
  trading.py         # 下单：market_buy/sell、limit_buy/sell，支持 tif=DAY/OPG
  account.py         # 账户余额 + 持仓查询，自动快照存库
  market_data.py     # 实时行情订阅 + 价格报警
  database.py        # SQLite：orders / account_snapshots / signals / scheduled_tasks / config_store / pending_exits（半自动出场待确认）
                     #   注：klines 表已移除，K 线数据统一由 DataStore / IBKRDataStore 管理
  historical_data.py # IBKR K 线拉取封装，完全委托 IBKRDataStore（Parquet），db 参数已废弃
  universe.py        # 股票池：get_tickers(universe) 支持 sp500/nasdaq100/russell2000
  data_store.py      # Parquet 本地数据存储（yfinance），回测/实盘选股共用同一份数据
                     #   stale check 行为：end 接近今天时过滤退市股；历史回测时放行（含已退市）
  ibkr_data_store.py # Parquet 本地数据存储（IBKR），存 data/stocks_ibkr/，仅数据验证用
  earnings.py        # 财报日期查询 + 回避逻辑（prefetch_earnings / has_upcoming_earnings）
  insider.py         # OpenInsider 内幕买入数据抓取（20小时缓存）
  market_calendar.py # 美股交易日历：is_us_trading_day()，NYSE 假日表 2025-2027（超范围 fail-open+日志提醒）
                     #   auto_trader --run 休市日拒绝执行；market_is_open / exits._market_open_now 假日返回 False
                     #   （修复 2026-07-03 独立日补休当天下的 MKT/DAY 单被 IB 顺延到 7/6 开盘、绕过人工决策误卖的事故）
  logger.py          # 全局日志模块：logs/trading.log，每天切割，保留30天
  fmt.py             # 终端输出格式化工具（lj/rj 对齐函数）

# ── 策略层 ───────────────────────────────────────────────
strategies/
  base.py            # 抽象基类：generate_signals(df) → df（含 signal 列）
  rs_momentum.py     # 主策略：调用 factors/ 模块组合计算，输出买卖信号
                     #   支持 extra_filters 参数（注册表 filter 因子，验证后可推入生产）
  dynamic_factor.py  # Web 回测「单股回测」因子组合用：从注册表动态组合技术因子
  precompute.py      # 优化器专用：预计算全股票池因子，加速组合回测
  ma_crossover.py    # 均线交叉策略（辅助/测试用）
  ema_pullback.py    # 单股回测引擎：EMA21补仓 / 逢跌加仓 / RSMomentum / B&H 多策略对照（web「单股回测」tab 用）
  factors/           # 因子模块库（每个因子独立文件，瘦身后仅保留生产用技术因子）
    registry.py      # 因子注册表：get_registry() 返回所有因子元数据（共 8 个）
    rs_score.py      # 相对强度因子（个股 vs SPY）
    breakout.py      # 价格突破因子
    volume.py        # 成交量均线 / 放量突破 / 量价背离
    trend.py         # 趋势过滤（MA10 > MA20，短期趋势）
    drawdown.py      # 崩跌过滤（距高点最大回撤）
    atr.py           # ATR 波动率（供自适应止损使用，is_dependency）

# ── 运维工具 ─────────────────────────────────────────────
tools/
  compare_data.py    # yfinance vs IBKR 数据比对工具（检测价格/成交量差异）

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
  api/               # 路由：portfolio / factors / backtest / scheduler / config / optimizer
  services/          # 服务层：封装现有模块供 API 调用
  models.py          # Pydantic 请求/响应模型
  frontend/          # React + TS + Vite 前端
    src/pages/       # 各功能页面组件（Portfolio/MarketScan/Backtest/AITracker/AStockTracker/Optimizer/Scheduler/Config 等）
    dist/            # 生产构建（npm run build 生成，FastAPI 静态服务）
start_web.sh         # 一键启动脚本
```

---

## RS 动量策略买入/卖出逻辑

文件：`auto_trader.py` → `scan_signals()` + `_execute_inner()` + `strategies/rs_momentum.py`

---

### 第一步：策略信号生成（RSMomentum，双路扫描）

`auto_trader.scan_signals` 同时用**两套参数**跑 RSMomentum，按股票所在池分流：
- **SP500 普通池**走严格参数（默认 5 条件）
- **AI 优先池**（`data/ai_universe.json` 成员）走宽松参数：`proximity_pct=15%`（吃趋势中段，不等突破）、`vol_multiplier=0`（取消放量要求，MU 类持续突破票常缩量上涨）

|  条件 | SP500 严格版 | AI 优先版 |
|------|------|------|
| RS 跑赢 SPY (`rs_period=63`) | ✅ 必须 | ✅ 必须 |
| 接近高点 (`proximity_pct`) | 5%（高点95%内） | **15%**（高点85%内） |
| 放量 (`vol_multiplier`) | 1.5x | **0（跳过）** |
| 崩跌过滤 (`max_drawdown=-30%`) | ✅ 必须 | ✅ 必须（保留防御） |
| 趋势 MA10>MA20 | ✅ 必须 | ✅ 必须 |

**卖出信号（量价背离）：** 价格创50日新高但成交量低于均量 × `VOL_SHRINK_RATIO`（顶部信号）。**实盘已不据此自动卖出**（`SELL_ON_ALERT=False`，出场见「止损体系（半自动版）」：-15%/EMA21 触发→人工确认，-25% 灾难线全自动）；该信号现仅供 `sp500_scanner` 报警参考。

> **AI 优先池如何维护**：在 Web UI「美股AI追踪」(`/#/ai`) 增删股票即可写入 `data/ai_universe.json`；`auto_trader` 每次扫描自动加载（无需重启）。用户的主观判断（看好 MU/NVDA/AVGO）通过这个接口传给系统。

---

### 第二步：市场环境熔断（scan_signals 阶段）

以下任一条件触发时，**清空全部买入信号**（卖出报警照常输出）：

| 熔断类型 | 触发条件 | 参数 |
|----------|----------|------|
| SPY 熔断 | SPY 近 N 日跌幅 ≤ `SPY_BRAKE_PCT` | `SPY_BRAKE_PERIOD=20`，`SPY_BRAKE_PCT=-8%` |
| VIX 熔断 | VIX 收盘 ≥ `VIX_BRAKE_LEVEL` | `VIX_BRAKE_LEVEL=30` |

市场宽度不足时**不清空**信号，但在执行阶段压缩仓位上限为 `BREADTH_MAX_POS`：

| 条件 | 参数 | 说明 |
|------|------|------|
| 宽度压仓 | `BREADTH_MIN_PCT=35%` | S&P500 中站上 MA200 的比例低于此值时，最多只开 `BREADTH_MAX_POS=4` 仓 |

---

### 第三步：买入候选过滤链（scan_signals 阶段）

信号通过策略后，在进入执行前按顺序过滤（**任一不满足则跳过该股**）：

| 过滤项 | 条件 | 参数/说明 |
|--------|------|-----------|
| 市值下限 | `market_cap_b ≥ MIN_CAP_B` | 默认 10B，无市值数据时放行 |
| 市值上限 | `market_cap_b ≤ MAX_CAP_B` | 默认 5000B（实际不过滤 mega-cap） |
| 行业黑名单 | 行业名包含 `DENY_INDUSTRIES` 中任一关键词 | 默认 `['Software—Application']`，模糊匹配 |
| ROE | `roe ≥ FUND_MIN_ROE`（`FUND_FILTER_ENABLED=True` 时生效） | 默认 0.0，排除亏损公司；无数据时放行 |
| 负债权益比 | `debt_to_equity ≤ FUND_MAX_DE` | 默认 2.0，高杠杆行业（如中游能源）常在此被过滤 |
| 营收增长 | `revenue_growth ≥ FUND_MIN_REV_GROWTH` | 默认 -20% |

---

### 第四步：入场得分排序

通过过滤的候选按**复合入场得分**降序排列（得分高者优先获得仓位槽）：

```
entry_score = rs_score × (1 + vol_boost + proximity_boost + insider_boost + ai_boost) + ai_priority_bonus

vol_boost          = min(vol_ratio / 3.0, 1.0) × 0.15      # 量比加成，最高 +15%（3x封顶）
proximity_boost    = max(0, (drawdown + 0.30) / 0.30) × 0.10  # 近高点加成，最高 +10%
insider_boost      = min(insider_score / 10.0, 1.0) × 0.10  # 内幕买入加成，最高 +10%
ai_boost           = 0.10（在AI池中）或 (score/15)×0.20（有AI评分时，最高 +20%）
ai_priority_bonus  = +0.5（AI 优先池成员绝对置顶；rs_score 通常 [-0.3, +0.5]，bonus 保证 AI 票排在 SP500 普通票之上）
```

---

### 第五步：执行阶段过滤（execute 阶段，每只候选逐一检查）

| 检查项 | 跳过条件 |
|--------|----------|
| 已持仓 | `symbol in stock_positions` |
| 行业集中度 | 该 GICS 行业已持有 ≥ `MAX_PER_SECTOR`（默认3）只。**AI 优先池成员豁免**（GPU/网络/电力本来就重叠半导体，且不计入计数，与 SP500 池独立） |
| 非 AI 主线倾斜 | 非 AI 优先池的纯动量票已持有 ≥ `MAX_NON_AI_POS`（默认1，含存量口径）则跳过。**AI 优先池成员豁免**。让书向 AI 主线集中，又保留少量宽基动量机会（如 D 电力、DAL 航空这类 off-theme 票被限到最多 1 只） |
| 财报回避 | N 日历日内有财报，`EARNINGS_AVOID_DAYS=1` |
| 仓位槽位 | `executed >= slots`（已满则停止） |
| 资金不足 | `deployable < budget_per_pos × 0.5` |
| VIX熔断 | `_vix_brake=True` 则清空买入列表 |
| 单价超预算 | `qty <= 0`（按净值比例/ATR风险法计算后股数为零） |

**仓位计算（取两者较小值）：**
- 风险法：`qty = (net_liq × TARGET_RISK_PER_POS) / (ATR_STOP_MULTIPLIER × ATR14)`
- 比例法：`qty = net_liq × POSITION_PCT / close_price`
- 无 ATR 数据时退回：`qty = budget_per_pos / close_price`

**OPG 限价保护：** 买入限价 = 昨收 × (1 + `MAX_ENTRY_SLIPPAGE`)，开盘跳空超阈值则自动放弃。部分成交时 9:32 ET 后补挂 DAY 限价单。

---

### 止损体系（半自动版：触发 → 人工确认，仅灾难线全自动）

实盘 `auto_trader._execute_inner` 的出场按顺序检查（现金等价 ETF 跳过）。设计动机：纯数学止损不区分「个股利空」vs「板块被外围带崩后龙头已反弹」（SNDK 误割教训），卖/留判断交给人 + Claude 情报，程序只负责触发与灾难兜底：

| 优先级 | 类型 | 触发条件 | 处理方式 |
|--------|------|----------|----------|
| 0 | **灾难硬止损（不可否决）** | 浮亏 `ret ≤ DISASTER_STOP_PCT`（默认 **-25%**） | **全自动**直接挂卖单（OPG限价/盘中市价），并写 `pending_exits` 审计记录（status=auto_sold） |
| 1 | 硬止损 | 浮亏 `ret ≤ STOP_LOSS_PCT`（默认 -15%，按现价/盘前价算） | **写 `pending_exits` 待确认记录，不下单** |
| 2 | EMA21 两日破位 | 最近两根**已收盘**日线（T-1、T-2）收盘均 < 各自当日 `EMA21`（`EMA_EXIT_PERIOD=21`，代码内常量） | 同上，待人工确认 |

**半自动确认流程（规则 1/2）：**
- 触发后 auto_trader 写 DB `pending_exits` 表（同一 symbol 当日 upsert 去重，9:00 OPG 与 9:35 exits-only 双跑不重复），随后 best-effort 调 `web/services/intel_svc.py` 拉 Claude 情报（个股新闻/板块龙头动向/是否系统性恐慌，约 1-3 分钟，失败不影响记录）
- Web UI 持仓页顶部出「待确认出场」卡片（导航栏红点计数，60s 轮询 `/api/exits`）：触发原因 + Claude 情报 + 「确认卖出 / 保留持仓」按钮
- **确认卖出** → `web/api/exits.py` 走 `portfolio_svc.place_sell_order`（盘中 MKT/DAY，盘外 LMT/OPG 下限=触发价×0.95）；下单成功才标记 sold
- **保留持仓** → 标记 kept，**并撤销 IB 上该标的全部遗留 SELL 挂单**（best-effort，撤单失败前端弹警告提示手动检查）；**当日**不再重复提醒；次日触发条件仍成立会重新建记录提醒
- **未确认默认保留**（不卖、不腾槽、不回笼资金），下跌风险由灾难线兜底；触发条件消失（反弹/已手动卖出）的旧记录每轮自动标记 expired

> **历史多层止损（ATR自适应 / EMA8破位 / 移动止损 / 时间止损 / 量价背离卖出）已从实盘下线。** `SELL_ON_ALERT` 恒 False。这些机制仍存在于两处，**不影响实盘执行**：
> - `tests/backtest_rs.py --exit-mode legacy`（默认，与「回测业绩参考」表一致；`--exit-mode simple` 才对齐实盘 2 条规则）
> - `sp500_scanner.py` 的移动止损是**持仓报警**（提示用），不下单
>
> `ATR_STOP_MULTIPLIER` / `ATR_STOP_FLOOR` 在实盘仍被使用，但仅用于**仓位计算**（风险法 qty），不再作为出场条件。`TRAIL_STOP_*` / `TIME_STOP_*` / `EMA_STOP_PERIOD` 参数保留是给回测 legacy 模式和扫描器报警用。

---

### 定向个股情报引擎（`web/services/intel_svc.py`）

**双引擎自动选择**（env `INTEL_ENGINE`=auto/cli/api，默认 auto）：
- **cli（默认优先）**：本机 `claude` CLI 无头模式（`-p` + WebSearch），走 **Claude 订阅额度**（OAuth），零 API 费用；子进程剥掉 `ANTHROPIC_API_KEY` 防误走 API 计费；模型默认 sonnet（env `INTEL_CLI_MODEL` 可改）
- **api 兜底**：Anthropic API（claude-opus-4-8 + web_search server tool），需 API key 且有余额（用户账户当前无额度且不打算充值，故实际走 cli）

两条业务线共用（另有零成本兜底：待确认出场卡片始终显示 yfinance/SEC 新闻标题，不经 LLM）：

1. **出场情报**（`generate_exit_intel` / `enrich_pending_exits`）：对触发出场的持仓检索个股新闻、板块龙头动向（如存储链看 SK 海力士），判断下跌归因（个股利空/板块拖累/系统性恐慌），给「卖出/保留/减半观察」倾向建议。auto_trader 触发后自动拉，Web UI 待确认卡可手动重试
2. **核心票每日情报卡**（`generate_core_cards`，CLI `python -m web.services.intel_svc --core-cards`）：对盘前清单 core 组每只票出卡（隔夜要闻/产业链同行/华尔街/催化剂倒计时），并对照手填的**持有逻辑+失效条件**给论点检查档位（强化/中性/削弱/失效预警）。缓存 `data/.core_cards_cache.json`；入口：顶栏「盘前扫描」modal →「核心票情报卡」tab；调度任务 `core_intel_cards`（默认关闭，美东 8:15）
   - core 组配置在 `data/premarket_config.json`（「清单配置」tab 手填），字段：ticker/cost/weight/thesis/**invalidation（失效条件）**/**catalysts（催化剂日历）**——后两个字段是论点检查的依据
   - 每次生成 = 一次 Claude+联网调用；cli 引擎消耗订阅额度（无现金成本），api 引擎 5 只票约 $0.5-1.5

---

### MSS 市场强度评分（Market Strength Score）

`core/market_regime.py::compute_mss()` — 每次执行前计算，驱动**仓位上限**的自适应切换。（注：移动止损已从实盘下线，MSS 不再调止损参数，`MSS_*_TRAIL_*` 参数仅回测 legacy 模式可能引用。）

```
MSS = SPY趋势分 + 市场宽度分 + VIX得分   ∈ [-1, +1]

SPY趋势分  = (SPY > MA20)×0.2 + (SPY > MA50)×0.2   → [0, 0.4]
市场宽度分  = breadth_pct × 0.4                       → [0, 0.4]
VIX得分    = clip((30 - VIX) / 100, -0.2, +0.2)      → [-0.2, +0.2]
```

| MSS 区间 | 市场状态 | 生效的仓位上限 |
|----------|----------|----------|
| ≥ 0.5 | 强牛市 | `MSS_BULL_MAX_POS` |
| 0.0 ~ 0.5 | 温和 | `MAX_POSITIONS`（默认值） |
| < 0.0 | 弱势/熊市 | `MSS_BEAR_MAX_POS`（压缩仓位） |

---

## 风控参数（$60K 资金配置）

所有参数统一在 `config.py` 定义默认值，运行时从 DB `config_store` 表读取（Web UI 系统配置页可修改）。**任何地方不要硬编码这些值。**

**仓位管理：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_POSITIONS` | 6 | 最多同时持有只数 |
| `POSITION_PCT` | 0.15 | 每仓占净值比例（15% ≈ $9,000/仓） |
| `CASH_RESERVE_PCT` | 派生 | = 1 - MAX_POSITIONS × POSITION_PCT，自动计算 |
| `MAX_PER_SECTOR` | 3 | 同一 GICS 行业最多持有只数 |
| `MAX_NON_AI_POS` | 1 | 非 AI 优先池的纯动量票最多持有只数（含存量口径，AI 池不受限）；设大值如 99 = 不限制 |
| `TARGET_RISK_PER_POS` | 0.03 | 每仓目标风险比例（ATR止损触发最大亏损） |

**止损参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `STOP_LOSS_PCT` | -0.15 | 硬止损触发线（触发后挂「待确认出场」，人工决定卖/留） |
| `DISASTER_STOP_PCT` | -0.25 | 灾难硬止损线（不可否决，触发即自动卖出兜底） |
| `ATR_STOP_MULTIPLIER` | 2.5 | ATR自适应止损倍数 |
| `ATR_STOP_FLOOR` | -0.20 | ATR止损最大亏损下限（防止高波动股止损太宽） |
| `EMA_STOP_PERIOD` | 8 | EMA 破位止损周期（收盘 < EMA-N 触发，0=禁用） |
| `TRAIL_STOP_ACTIVATE_PCT` | 0.08 | 浮盈超过此值后启用移动止损 |
| `TRAIL_STOP_PCT` | -0.07 | 从峰值回撤超过此值触发移动止损 |
| `TIME_STOP_DAYS` | 20 | 时间止损观察期（交易日数，0=禁用） |
| `TIME_STOP_MIN_RETURN` | 0.05 | 时间止损最低盈利门槛 |

**熔断/过滤：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SPY_BRAKE_PERIOD` | 20 | SPY 观察窗口（交易日数） |
| `SPY_BRAKE_PCT` | -0.08 | SPY 近 N 日跌超此幅度时暂停买入 |
| `VIX_BRAKE_LEVEL` | 30 | VIX 超过此值时暂停新建仓 |
| `BREADTH_MIN_PCT` | 0.35 | S&P500 中站上 MA200 的比例低于此值时触发宽度过滤 |
| `BREADTH_MAX_POS` | 4 | 市场宽度不足时最多持有的仓位数 |
| `MIN_CAP_B` | 10.0 | 最小市值（十亿美元，排除微盘股） |
| `MAX_CAP_B` | 5000.0 | 最大市值（十亿美元，实际不过滤 mega-cap） |
| `DENY_INDUSTRIES` | `['Software—Application']` | 拒绝行业关键词（模糊匹配） |
| `FUND_FILTER_ENABLED` | True | 是否启用基本面硬门槛（无数据时放行） |
| `FUND_MIN_ROE` | 0.0 | ROE 最低门槛（排除亏损公司） |
| `FUND_MAX_DE` | 2.0 | 负债权益比上限 |
| `FUND_MIN_REV_GROWTH` | -0.20 | 营收增长最低门槛 |
| `EARNINGS_AVOID_DAYS` | 2 | 财报前 N 日历日内不开新仓（0=禁用） |
| `MAX_ENTRY_SLIPPAGE` | 0.01 | OPG 买入限价保护：最多接受昨收 +1% |

**MSS 市场强度自适应参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MSS_BULL_THRESHOLD` | 0.5 | MSS ≥ 此值视为强牛市 |
| `MSS_BEAR_THRESHOLD` | 0.0 | MSS < 此值视为弱势/熊市 |
| `MSS_BULL_MAX_POS` | 6 | 强牛市时最大仓位数（与默认 MAX_POSITIONS 相同，不额外加仓） |
| `MSS_BULL_TRAIL_ACTIVATE` | 0.08 | 强牛市时移动止损激活门槛（与默认相同） |
| `MSS_BULL_TRAIL_PCT` | -0.07 | 强牛市时移动止损触发幅度（与默认相同） |
| `MSS_BEAR_MAX_POS` | 4 | 弱势市场时仓位上限（压缩至4仓） |
| `MSS_BEAR_TRAIL_ACTIVATE` | 0.05 | 弱势市场时移动止损激活门槛（浮盈5%即激活） |
| `MSS_BEAR_TRAIL_PCT` | -0.05 | 弱势市场时从峰值回撤5%即触发（更紧） |

**内幕数据配置：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `INSIDER_DAYS` | 30 | 内幕买入观察窗口（天） |
| `INSIDER_MIN_VALUE_K` | 100 | 内幕单笔最小金额（千美元） |

**策略参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `INITIAL_CASH` | 60,000 | 回测初始资金（实盘忽略） |
| `VOL_SHRINK_RATIO` | 0.7 | 量价背离判定：成交量低于均量×此值触发 |

---

## 因子系统架构

**因子注册表**（`strategies/factors/registry.py`）是整个因子体系的核心。**已瘦身**：只保留 RSMomentum 生产策略实际用到的 8 个技术因子（5 个买卖条件 + 2 个依赖项 + 1 个卖出报警），不再收录从未投产的实验因子（动量质量 / OBV / 波动率过滤 / 行业相对强度）和基本面快照因子。

| 因子 key | 类型 | signal_type | 说明 |
|----------|------|-------------|------|
| `rs_score` | technical | score | RS 相对强度（个股 vs SPY） |
| `breakout` | technical | filter | 价格突破近50日高点 |
| `volume_ma` | technical | score | 成交量均线（is_dependency） |
| `volume_surge` | technical | filter | 放量突破（量 > 均量×倍数） |
| `volume_divergence` | technical | sell_alert | 量价背离顶部信号 |
| `trend_filter` | technical | filter | MA10 > MA20 短期趋势过滤 |
| `drawdown_filter` | technical | filter | 崩跌过滤（距高点最大回撤） |
| `atr` | technical | score | ATR14（is_dependency，供止损用） |

**关键设计原则：**
- `RSMomentum`（生产策略）硬编码核心5个条件，不读注册表。仍支持 `extra_filters` 参数传入额外注册表 filter 因子（机制保留，但当前注册表内 filter 因子已全部是核心条件）
- `DynamicFactorStrategy`（Web 回测「单股回测」因子组合 / 优化器）从注册表动态组合技术因子，支持 `set_spy()`
- **基本面快照（PE/PB/ROE/营收·盈利增长/FCF 等）不在注册表里**：由 `core.universe.get_stock_info` 直接产出，在市场扫描表 / K 线详情基本面 tab 展示，不参与时序信号 / 回测 / 优化
- 因子开关（`FACTOR_*_ENABLED`）存在 DB `config_store` 表，只影响「市场扫描 / 优化器 / 单股回测」用的 `DynamicFactorStrategy`，**不影响实盘 RSMomentum**（它硬编码核心条件不读注册表）。原「因子看板」管理 UI 已移除，如需增删开关/调参直接改 DB `config_store`
- 市场扫描的「临近财报标记」(`earnings_safe`) 由 `FACTOR_earnings_avoid_ENABLED` 配置驱动 + `factor_svc._earnings_safe`，独立于注册表（默认关闭）

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
信号生成（主数据源 yfinance）：
  yfinance → DataStore（data/stocks/ Parquet）→ RSMomentum.generate_signals() → 买卖信号
  core/earnings.py → 财报日期缓存 → 财报回避过滤
  core/insider.py  → OpenInsider 内幕数据 → 信号参考

  ★ DataStore 同一份数据同时服务回测和日常选股，无需两套：
    - 历史回测（end 为历史日期）：stale check 关闭，已退市股历史数据正常参与计算
    - 日常选股（end 接近今天）：stale check 开启，过滤数据已停更的退市/停牌股

数据质量验证（第二数据源 IBKR）：
  IBKR Gateway → IBKRDataStore（data/stocks_ibkr/ Parquet，~30只手动维护）
  tools/compare_data.py → 比对 yfinance vs IBKR OHLCV → 价格/成交量差异报告

实盘执行：
  auto_trader.py → core/trading.py → IBKR 下单 → core/database.py（orders 表）
  confirm_fills.py → ib.trades() → orders 表更新 filled_price → logs/trading.log

持仓报价（Web UI）：
  portfolio_svc.py → ib.reqHistoricalData() → 持仓实时报价（股票/期权均支持）

回测路径：
  DataStore → RSMomentum → tests/backtest_rs.py 输出报告

Web 优化路径：
  DataStore → strategies/precompute.py（预计算全因子）→ optimizer_svc 穷举组合 → 排名结果
```

### 数据比对工具

```bash
# 需要 IB Gateway 运行（模拟盘 4002）
python -m tools.compare_data --symbols AAPL NVDA MSFT --start 2024-01-01
python -m tools.compare_data --universe sp500 --sample 30 --start 2024-06-01

# 离线模式（已有 data/stocks_ibkr/ 缓存时可不连 IB）
python -m tools.compare_data --symbols AAPL --start 2024-01-01 --port 9999
```

**比对逻辑：** OHLC 差异 > 0.5% 标红，成交量差异 > 10% 标红。
**常见原因：** 价格差异通常源于复权调整（yfinance 复权价 vs IBKR 原始价），属正常现象；成交量差异源于 yfinance 可能含盘后。

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

---

## A 股动能轮动（辅助功能）

不同于美股 RS 动量的核心地位，A 股部分定位为**辅助/研究工具**：股票池策展（AI 硬件主题板块）+ 动能扫描 + 回测验证。数据源 **akshare**（国内直连），暂不接入实盘交易。

### 股票池：AI 产业链主题板块

20 个手动维护板块（`data/astock_themes.json`），约 186-188 只，覆盖 AI 硬件全链：
GPU/算力芯片、CCL、玻纤、半导体材料/设备、模拟/电源芯片、功率半导体、存储、晶圆代工、封测、被动元件、服务器、光模块、光纤、PCB、连接、液冷、电源、IDC、电网。

Web UI「A股AI追踪」(`/#/astock`) 增删股票 → 自动写入 themes.json → 扫描/回测立即生效。`astock_universe.add_theme_stock` / `remove_theme_stock` 为后端接口。

### 评分公式 composite（0-10 综合分）

```
composite = 0.35 × z_mom_5d        ← 5 日相对沪深300超额(z-score 归一到 0-10)
          + 0.20 × z_mom_3d        ← 3 日超额
          + 0.20 × z_rs_vs_group   ← 个股 vs 板块中位的差(板块内强度)
          + 0.15 × z_vol_ratio     ← 近 3 日 / 近 20 日量比
          + 0.10 × flow_score      ← OBV 斜率 + 上涨量占比(资金流向)
          + 0.5（若加速：3日均涨 > 5日均涨）
```

每只票额外字段 `group_rank`（板块内排名,1=板块第1）和 `group_size`。前端「板块强度」列用 ★★★/★★/★/#N 展示。

> ⚠️ **"板块强度第 1" ≠ 机构龙头**。无市值/北上/龙虎榜数据,纯短期动量+量能。小盘股容易冲到板块第 1，真机构票（如寒武纪 4000 亿市值）可能因短期未拉升排到第 3。升级路径：加流通市值过滤（B 方案，未落地）或接入北上资金（C 方案）。

### 回测：4 个可选策略（`/#/backtest?tab=astock`）

每周一开盘价 rebalance，持有 composite 选股前 N 等分，**A 股按手交易（整百股）**，沪深300 基准。

| 策略 | 选股逻辑 | 适用市况 |
|------|---------|----------|
| `momentum` | composite 前 N（基线） | 趋势市，顶部易翻车 |
| `momentum_filtered` | composite 前 N **且** 收盘 ≥ EMA21 | 牛市基线 + 个股趋势防御 |
| `sector_rotation` | 板块强度（组内 composite 中位）前 2，每板块取 composite 前 2 只 | AI 主升期，集中放大 alpha |
| `quality_momentum` | composite × (1+0.5×归一化 EP)，PE 越低权重越高 | 长期持有，过滤估值过高 |

**按手交易意义**：资金 10 万时单仓 ¥25,000 预算下，高价股（寒武纪 ¥1196、茅台 ¥971）买不到 1 手会自动跳过，贴近实盘约束。

**2025-04 ~ 2026-05（14 个月）回测参考**（HS300 同期 +26%）：
| 策略 | 总收益 | 超额 | Sharpe | 回撤 |
|------|--------|------|--------|------|
| `momentum` | +253% | +228% | 2.23 | -31% |
| `momentum_filtered` | +265% | +239% | 2.28 | -31% |
| `sector_rotation` | **+625%** | **+600%** | 2.92 | -33% |
| `quality_momentum` | +253% | +228% | 2.23 | -31% |

> ⚠️ 2025 年 A 股 AI 硬件主题为结构性大牛市，**回测属于"样本内最佳时段"**。实盘印花税 0.05% + 佣金 0.025% + 滑点 0.1%，按 60 次/年 rebalance + 50% 平均换手率估算，**实际可拿到约 6-8% 年化拖累**，需在回测结果上打折。

### 数据与缓存

- **数据源**：akshare 多源兜底（主 sina，备 东方财富、腾讯）。sina 用 mini-racer 解密，**模块级 `_sina_lock` 串行**（线程不安全）。
- **本地存储**：`data/stocks_a/{code}.parquet`（含 OHLCV + shares 流通股本）、`data/stocks_a/_idx_HS300.parquet` 等基准指数。
- **代理处理**：`_ensure_cn_direct()` import 时把国内域名追加进 `no_proxy`。**Clash TUN/增强模式（透明代理）下 `no_proxy` 不生效**，akshare 会失败。
- **缓存 TTL**：动能扫描 30 分钟，PE 快照 24 小时，股票名称表 24 小时。
- **历史窗口**：`_do_scan` 默认 530 天预热（兼顾实时扫描和回测），首次预热 sina 串行约 5-8 分钟。

### 关键 Bug（已修，避免重踩）

**`AStockDataStore._update_one` earliest 检查缺失**：此前只判断"本地最新 vs 今天"，忽略 `start` 参数。请求 `start='2025-01-01'` 时若本地只有 90 天数据，**不会回补历史**，回测长窗口里组合会一直静止持现金（典型现象：206/242 天 portfolio = initial_cash）。修复方案：本地起点 > 请求 start 时从 start 重拉，merge dedup(keep='last') 自动覆盖重叠。

### 调度任务（默认 disabled，需在 `/#/scheduler` 手动开启）

| Task ID | 说明 | Cron（北京时间） |
|---------|------|----------------|
| `astock_update` | A 股盘中实时刷新：每 30 分钟用实时快照覆盖当日 bar + 主题板块扫描重建缓存 | `0,30 10-11,13-15 * * 1-5`（交易时段每 30 分钟，10:00~11:30 / 13:00~15:30） |
| `astock_refresh` | 次日早用 sina 正式前复权日线覆盖前一日盘后快照 bar | `30 7 * * 2-6` |

### 用户配置文件跨电脑同步

`data/ai_universe.json` 和 `data/astock_themes.json` 在 `.gitignore` 中**白名单例外**（其余 `data/*` 仍忽略）。改动需 `git add data/ai_universe.json data/astock_themes.json` 显式提交，避免在 A 电脑改了 B 电脑看不到。
