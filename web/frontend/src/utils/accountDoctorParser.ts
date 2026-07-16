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

/** 按证券代码分块，使用持仓量×最后价计算市值，避免导出列错位。 */
export function parsePositionsBlock(text: string, krwRate: number): DoctorPosition[] {
  const lines = stripThousands(text).split('\n').map((line) => line.trim()).filter(Boolean)
  const positions: DoctorPosition[] = []
  let current: { symbol: string; name: string; nums: number[]; krw: boolean } | null = null

  const flush = () => {
    if (!current) return
    const [quantity, last] = current.nums
    if (quantity != null && last != null) {
      const marketValue = current.krw
        ? (quantity * last) / (krwRate || DEFAULT_KRW_RATE)
        : quantity * last
      positions.push({
        symbol: current.symbol,
        name: current.name || undefined,
        market_value_usd: Math.round(marketValue),
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
