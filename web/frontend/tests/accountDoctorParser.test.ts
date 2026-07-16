import assert from 'node:assert/strict'
import test from 'node:test'

import { parseAccountBlock, parsePositionsBlock } from '../src/utils/accountDoctorParser.ts'

test('解析美股与韩股持仓并按最后价计算市值', () => {
  const text = `
产品 持仓 最后价 变动% 成本基础 市场价值
MU 美光科技股份有限公司
40 976.63 -5.39% 40,477 39,065.20
000660 SK 海力士株式会社
8 2,424,000 +10.84% 2070万 19,392,000
`
  const rows = parsePositionsBlock(text, 1350)

  assert.equal(rows.length, 2)
  assert.deepEqual(rows[0], {
    symbol: 'MU', name: '美光科技股份有限公司', market_value_usd: 39065,
    theme: '其它', leverage_factor: 1, is_leveraged: false, currency: 'USD',
  })
  assert.equal(rows[1].symbol, '000660')
  assert.equal(rows[1].currency, 'KRW')
  assert.equal(rows[1].market_value_usd, 14364)
})

test('忽略没有数量或最后价的不完整证券块', () => {
  assert.deepEqual(parsePositionsBlock('MU 美光科技\n— —', 1350), [])
})

test('解析 USD 后裸净值以及分行账户字段', () => {
  const text = `
账户
U12345678
USD
77,547.06
已结算现金
-11,376.92
维持保证金
53,504.08
剩余流动性
24,030.19
未实现盈亏 -1,410.50
`
  assert.deepEqual(parseAccountBlock(text), {
    net_liq: 77547.06,
    settled_cash: -11376.92,
    maint_margin: 53504.08,
    excess_liquidity: 24030.19,
    unrealized_pnl: -1410.5,
  })
})

test('账户字段首次出现优先，不被后续同名字段覆盖', () => {
  assert.deepEqual(parseAccountBlock('净值 100\n净清算 200'), { net_liq: 100 })
})
