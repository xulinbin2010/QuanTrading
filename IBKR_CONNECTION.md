# IBKR 连接说明（供其他 Claude CLI 会话使用）

## 依赖

```bash
pip install ib_insync
```

IB Gateway 必须提前启动并开启 API（Configuration → API → Enable ActiveX and Socket Clients）。

## 端口

| 模式 | IB Gateway | TWS |
|------|-----------|-----|
| 模拟盘 | 4002 | 7497 |
| 实盘 | 4001 | 7496 |

**实盘操作前须关闭 Gateway 的"只读"模式。**

## 最简连接

```python
from ib_insync import IB, Stock, Option

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=1)   # 模拟盘
# ib.connect('127.0.0.1', 4001, clientId=1)  # 实盘
print(ib.managedAccounts())
```

## 本项目封装的连接类

```python
import sys
sys.path.insert(0, '/Users/xulinbin/aiProject/QuanTrading')

from core.connection import IBConnection

conn = IBConnection(host='127.0.0.1', port=4002, client_id=1, timeout=60)
ib = conn.connect()   # 返回 ib_insync.IB 实例，含自动重连
# ...
conn.disconnect()
```

## 期权数据示例

```python
from ib_insync import IB, Stock, Option

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=2)  # clientId 与其他会话区分开

# ── 1. 查期权链（到期日 + 行权价列表）──────────────────────
chains = ib.reqSecDefOptParams('AAPL', '', 'STK', 0)
for chain in chains:
    print(chain.exchange, chain.expirations, chain.strikes)

# ── 2. 构建期权合约并 qualify ────────────────────────────
contract = Option('AAPL', '20251219', 200, 'C', 'SMART')
ib.qualifyContracts(contract)

# ── 3. 实时快照（Greeks / IV）────────────────────────────
[ticker] = ib.reqTickers(contract)
g = ticker.modelGreeks
if g:
    print(f"delta={g.delta:.3f}  gamma={g.gamma:.4f}  "
          f"vega={g.vega:.3f}  theta={g.theta:.3f}  iv={g.impliedVol:.3f}")

# ── 4. 历史 K 线 ─────────────────────────────────────────
bars = ib.reqHistoricalData(
    contract,
    endDateTime='',
    durationStr='30 D',
    barSizeSetting='1 day',
    whatToShow='TRADES',
    useRTH=True,
)
import pandas as pd
df = pd.DataFrame([{'date': b.date, 'close': b.close, 'volume': b.volume} for b in bars])

# ── 5. 持仓（含期权）─────────────────────────────────────
for pos in ib.positions():
    c = pos.contract
    print(c.secType, c.symbol,
          getattr(c, 'right', ''), getattr(c, 'strike', ''),
          getattr(c, 'lastTradeDateOrContractMonth', ''),
          pos.position, pos.avgCost)

# ── 6. 账户净值 ───────────────────────────────────────────
vals = {v.tag: v.value for v in ib.accountValues() if v.currency == 'USD'}
print('净值:', vals.get('NetLiquidation'))

ib.disconnect()
```

## 注意事项

- 同一个 IB Gateway 同时只允许有限个 clientId 并发连接（通常 ≤32），本项目用 clientId=1，新会话用 2、3… 避免冲突。
- `reqTickers` 需要行情订阅权限（Market Data Subscriptions）；若返回的 Greeks 为 None，说明没有该合约的行情权限。
- 模拟盘账户的期权行情有时延迟或缺失，实盘账户更完整。
- `ib_insync` 内部有 event loop，在 Jupyter / asyncio 环境下需要用 `nest_asyncio` 或 `await` 方式。
