# QuanTrading 优化待办

> 最后更新：2026-04-13
> 记录系统中已知的改进点，按优先级分组。

---

## 🔴 高优先级（影响结果准确性）

### 1. 幸存者偏差：股票池用当前成分而非历史点位成分
**问题：** `universe.py` 每次从 iShares/Wikipedia 拉当前 S&P 500 成分，回测历史年份（2022/2023）
时用的是"今天的成分"，而不是"当时的成分"。被剔除/退市的股票在当年其实在池子里，
但现在被排除了，导致策略回测偏乐观。
**影响程度：** 中等。本策略是强动量过滤，大部分退市股早就触发止损出局；但确实存在偏差。
**可选方案：**
- 维护一份 `data/sp500_history.csv`，记录每次成分变更（日期 + 加入/退出）
- 回测时用 `get_sp500_at(date)` 取当日历史成分
- 数据源：Quandl S&P 500 历史成分（免费），或手动维护季度变更

### ~~2. 调试端点残留生产代码~~ ✅ 已完成
`/api/portfolio/price-test` 已删除（2026-04-13）。

---

## 🟡 中优先级（影响策略质量）

### ~~3. 回测缺乏 Walk-Forward 验证~~ ✅ 已完成
Walk-Forward 页面已实现（2026-04-12）：回测页新增 Tab，支持滚动窗口 IS vs OOS 对比。

### （原 #3）
**问题：** 目前只做固定区间回测（如 2022-2025 全量），参数（如 `rs_period=63`、
`breakout_period=50`）是在同一段历史上调的，存在过拟合风险。
**改进：** 在回测页面或脚本增加滚动窗口（Walk-Forward）模式：
- 训练窗口：前 N 年调参 → 测试窗口：后 1 年 OOS → 滚动前进
- 目的不是调参，而是验证策略在 OOS 样本上是否一致

### ~~4. 止损参数用固定比例，未充分利用已有的 ATR~~ ✅ 已完成
`auto_trader.py` 已启用 `TARGET_RISK_PER_POS` ATR 动态仓位（2026-04-12）。

### （原 #4）
**问题：** 每仓分配固定 15% 净值（`POSITION_PCT=0.15`），但每只股波动率差异很大——
低波动股止损宽松浪费资金，高波动股头寸应更小。
**已有基础：** ATR14 已作为 `is_dependency` 因子计算，ATR 自适应止损逻辑已实现。
**改进方向：** 仓位大小按 `TARGET_RISK_PER_POS / ATR_stop_pct` 动态计算，
而不是固定 15%（已在 config 里有 `TARGET_RISK_PER_POS=0.03` 但实际 auto_trader 未启用）。

### 5. Sector ETF 数据每次扫描重新拉取
**问题：** `sector_rs` 因子依赖 11 个行业 ETF（XLK/XLF/XLV 等），每次市场扫描都
通过 DataStore 重新加载这 11 只 ETF 数据，与个股数据混在一起。
**改进：** ETF 数据单独预加载并缓存到内存（session 级别），避免每只股票触发 ETF 重读。
已有 `_load_price_map` 做了部分缓存，检查是否在 Web 扫描链路上生效。

### 6. 信号/成交无主动通知机制
**问题：** 买卖信号只能主动打开 Web UI 或跑 CLI 才能知道，成交回报也只写日志。
北京时间 22:00-22:35 需要人在电脑前确认。
**可选方案：**
- 最简单：成交确认后发一封邮件（Python smtplib，Gmail App Password）
- 更好：pushover / Telegram Bot 推送，手机可收到通知
- 优先级取决于是否真的需要实时响应

---

## 🟢 低优先级（体验优化）

### ~~7. DataStore `_load()` 每个股票读 Parquet 两次~~ ✅ 已完成
加入 `_last_date_cache`，`update()` 阶段缓存各 symbol 最新日期，`_load()` stale 检查直接用缓存，
不再全量读取后丢弃（2026-04-13）。

### （原 #7）
**问题：** 修复 stale check 后，`_load()` 对每只股票先读全量（检查最后日期），
再过滤日期范围。两次 `pd.read_parquet` 调用，500 只时性能有影响。
**修复：** 读一次全量，然后：
```python
df_full = pd.read_parquet(path)
if check_stale and not df_full.empty and df_full.index[-1] < stale_limit:
    ...  # 已是这个逻辑，但可以把 columns=['close'] 的轻量读改回去做第一步判断
```
实际上可以先只读 index（`pd.read_parquet(path, columns=['close']).index`）判断 stale，
通过后再读全量——避免全量读了却因 stale 直接丢弃。

### 8. 期权盘后/周末价格为 null
**现状：** 持仓中的期权在非交易时段通过 `reqHistoricalData` 尝试 MIDPOINT/BID_ASK，
但市场关闭时均返回 null，显示为 `-`。
**方向：** 可以 fallback 到上一个交易日的收盘价（已在 Parquet 或 IB Historical 里有），
而不是显示空白。影响不大，仅显示问题。

### 9. 因子注册表可考虑加入周线 RSI 作为可选过滤因子
**背景：** 日线 RSI > 85 过滤会误杀强势突破（与动量逻辑相悖），已讨论不推荐。
但周线 RSI（RSI_W > 80）更慢、误杀更少，可作为**可选实验因子**加入注册表，
通过优化器验证是否真的改善 Sharpe 再决定是否推入生产。
实现简单：对 `df['close'].resample('W').last()` 算 RSI，然后 reindex 回日线。

### 10. `_no_data_syms` 为 session 级别，多次 `update()` 调用间状态不一致
**问题：** 每次 `update()` 开始时 `self._no_data_syms = set()` 重置，
如果在一个 session 内对不同股票池多次调用 `update()`，前一次发现的无数据股票
在后一次 `_load()` 里不会被过滤（因为已重置）。
**低风险**：正常使用一般只调一次 `update()`，边界情况可通过日志观察。

---

## 📌 已确认不做的事项

| 想法 | 原因 |
|------|------|
| 删除退市股 Parquet 文件 | 会破坏历史回测完整性（幸存者偏差），数据应永久保留 |
| 日线 RSI > 85 买入过滤 | 与动量逻辑相悖，强势突破日 RSI 必然高，会误杀最好的信号 |
| 将 IBKR 数据用于回测 | IBKR 限速 12s/只，500只需100分钟；yfinance 批量下载更快，且自动复权 |
| 回测/选股分两套数据 | 同一份 yfinance 数据即可，stale check 区分历史/实盘场景 |
