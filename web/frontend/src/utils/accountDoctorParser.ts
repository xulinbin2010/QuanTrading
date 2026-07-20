export type DoctorPosition = {
  symbol: string
  name?: string
  market_value_usd?: number | string
  theme?: string
  leverage_factor?: number | string
  is_leveraged?: boolean
  currency?: string
}

export type DoctorAccount = {
  net_liq?: number | string
  maint_margin?: number | string
  excess_liquidity?: number | string
  settled_cash?: number | string
  unrealized_pnl?: number | string
}

export const DEFAULT_KRW_RATE = 1350

const numOf = (s: string): number | null => {
  const cleaned = s.replace(/[$,%\s]/g, '')
  return /^[+-]?\d+(\.\d+)?$/.test(cleaned) ? Number(cleaned) : null
}

const stripThousands = (text: string) => text.replace(/(?<=\d),(?=\d{3}(\D|$))/g, '')
const isTicker = (token: string) => /^[A-Z]{1,6}$/.test(token) || /^\d{6}$/.test(token)

type PositionValueMode = 'quantity_times_last' | 'direct_market_value'

/**
 * 先识别券商表头：
 * - 「最后价 / 现价 / Last」存在：前两个数字是持仓量、最后价，市值用两者反算；
 * - 只有「市场价值 / 市值」：第二个数字本身就是市值，不能再乘持仓量。
 *
 * 「平均价格」不是当前价。IB 的部分导出恰好是：
 *   持仓、市场价值、平均价格、未实现盈亏
 * 若仍固定做 quantity × second number，会把市值重复乘一次持仓数量。
 */
const detectPositionValueMode = (text: string): PositionValueMode => {
  const hasMarketValue = /市场价值|市场市值|市值|market\s*value/i.test(text)
  const hasLastPrice = /最后价|最新价|现价|当前价|收盘价|last(?:\s*price)?|mark(?:\s*price)?/i.test(text)
  return hasMarketValue && !hasLastPrice ? 'direct_market_value' : 'quantity_times_last'
}

const roundMoney = (value: number) => Math.round(value * 100) / 100

/** 按证券代码分块，并根据表头选择直接市值或「持仓量×最后价」。 */
export function parsePositionsBlock(text: string, krwRate: number): DoctorPosition[] {
  const valueMode = detectPositionValueMode(text)
  const lines = stripThousands(text).split('\n').map((line) => line.trim()).filter(Boolean)
  const positions: DoctorPosition[] = []
  let current: { symbol: string; name: string; nums: number[]; krw: boolean } | null = null

  const flush = () => {
    if (!current) return
    const [quantity, secondValue] = current.nums
    if (quantity != null && secondValue != null) {
      const nativeMarketValue = valueMode === 'direct_market_value'
        ? secondValue
        : quantity * secondValue
      const marketValue = current.krw
        ? nativeMarketValue / (krwRate || DEFAULT_KRW_RATE)
        : nativeMarketValue
      positions.push({
        symbol: current.symbol,
        name: current.name || undefined,
        market_value_usd: roundMoney(marketValue),
        theme: '其它',
        leverage_factor: 1,
        is_leveraged: false,
        currency: current.krw ? 'KRW' : 'USD',
      })
    }
    current = null
  }

  for (const line of lines) {
    const tokens = line.split(/[\s\t]+/).filter(Boolean)
    if (tokens.length && isTicker(tokens[0])) {
      flush()
      const symbol = tokens[0]
      const rest = tokens.slice(1)
      const name = rest.filter((token) => token !== '美元').join(' ')
      current = {
        symbol,
        name,
        nums: rest.map(numOf).filter((value): value is number => value != null),
        krw: /^\d{6}$/.test(symbol) || /韩|株式|海力士/.test(name),
      }
    } else if (current) {
      current.nums.push(...tokens.map(numOf).filter((value): value is number => value != null))
    }
  }
  flush()
  return positions
}

const ACCOUNT_KEYWORDS: [RegExp, keyof DoctorAccount][] = [
  [/净清算|净值|net.?liq/i, 'net_liq'],
  [/维持保证金|maint/i, 'maint_margin'],
  [/剩余流动性|excess/i, 'excess_liquidity'],
  [/已结算现金|settled/i, 'settled_cash'],
  [/未实现|unreali/i, 'unrealized_pnl'],
]

/** 解析账户总览；支持“标签+同行数值”、标签下一行数值及 USD 后裸净值。 */
export function parseAccountBlock(text: string): DoctorAccount {
  const lines = stripThousands(text).split('\n').map((line) => line.trim()).filter(Boolean)
  const account: DoctorAccount = {}

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const match = ACCOUNT_KEYWORDS.find(([pattern]) => pattern.test(line))
    if (match) {
      const key = match[1]
      const inline = line.split(/[\s\t]+/).map(numOf).find((value) => value != null)
      const value = inline ?? (i + 1 < lines.length ? numOf(lines[i + 1]) : null)
      if (value != null && account[key] == null) account[key] = value
      continue
    }
    if (/^[A-Z]{3}$/.test(line) && account.net_liq == null && i + 1 < lines.length) {
      const value = numOf(lines[i + 1])
      if (value != null) account.net_liq = value
    }
  }
  return account
}
