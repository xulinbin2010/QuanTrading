/**
 * AI 产业链公司元数据：中文名 + 一句话业务概括（用于「追踪清单」产业链图谱展示）。
 * 缺失的 symbol 在前端用 yfinance 行业兜底。新增成员补这里即可。
 */
export const AI_COMPANY_META: Record<string, { name: string; desc: string }> = {
  // ── AI 云服务 ──
  AMZN: { name: '亚马逊', desc: 'AWS云/自研AI芯片' },
  GOOGL:{ name: '谷歌', desc: '云/TPU自研芯片' },
  IBM:  { name: 'IBM', desc: '混合云/AI/量子' },

  // ── GPU/算力芯片 ──
  NVDA: { name: '英伟达', desc: 'GPU/AI算力龙头' },
  AMD:  { name: '超威', desc: 'CPU/GPU/MI加速卡' },
  INTC: { name: '英特尔', desc: 'CPU/代工/AI芯片' },
  AVGO: { name: '博通', desc: '定制ASIC/网络芯片' },
  MRVL: { name: '迈威尔', desc: '数据中心定制芯片' },
  QCOM: { name: '高通', desc: '移动SoC/边缘AI' },
  ALAB: { name: 'Astera Labs', desc: 'AI互连芯片' },
  TSM:  { name: '台积电', desc: '晶圆代工龙头' },
  ARM:  { name: 'Arm', desc: 'CPU架构IP' },
  TXN:  { name: '德州仪器', desc: '模拟/电源芯片' },
  ADI:  { name: '亚德诺', desc: '模拟/信号链' },
  MPWR: { name: '芯源系统', desc: '电源管理芯片' },
  MCHP: { name: '微芯', desc: 'MCU/模拟芯片' },
  NXPI: { name: '恩智浦', desc: '车用/边缘芯片' },
  SWKS: { name: 'Skyworks', desc: '射频前端' },
  NVTS: { name: 'Navitas', desc: '氮化镓功率芯片' },
  WOLF: { name: 'Wolfspeed', desc: '碳化硅功率器件' },
  POWI: { name: 'Power Int.', desc: '高压电源IC' },

  // ── 存储 ──
  MU:   { name: '美光', desc: 'DRAM/HBM/NAND' },
  SNDK: { name: 'SanDisk', desc: 'NAND闪存' },
  WDC:  { name: '西部数据', desc: '硬盘/存储' },
  STX:  { name: '希捷', desc: '大容量硬盘' },
  RMBS: { name: 'Rambus', desc: '内存接口IP' },
  DRAM: { name: 'DRAM ETF', desc: '存储ETF(海力士/三星/美光)' },
  SIMO: { name: 'Silicon Motion', desc: 'SSD主控芯片' },

  // ── 半导体设备/材料 ──
  LRCX: { name: '泛林', desc: '刻蚀/沉积设备' },
  KLAC: { name: '科磊', desc: '量检测设备' },
  AMAT: { name: '应用材料', desc: '沉积/刻蚀设备' },
  ASML: { name: '阿斯麦', desc: '光刻机龙头' },
  ONTO: { name: 'Onto', desc: '封装量测设备' },
  TER:  { name: '泰瑞达', desc: '芯片测试设备' },
  AMKR: { name: '安靠', desc: '封装测试' },
  ENTG: { name: '英特格', desc: '半导体材料/过滤' },
  AEHR: { name: 'Aehr', desc: '老化测试设备' },
  AXTI: { name: 'AXT', desc: '化合物衬底' },
  Q:    { name: 'Q', desc: '半导体相关' },
  TSEM: { name: 'Tower', desc: '特色代工/硅光' },
  CAMT: { name: 'Camtek', desc: '先进封装检测' },
  GFS:  { name: 'GlobalFoundries', desc: '晶圆代工' },

  // ── 服务器/数据中心硬件 ──
  SMCI: { name: '超微', desc: 'AI服务器整机' },
  DELL: { name: '戴尔', desc: '服务器/AI机柜' },
  HPE:  { name: '慧与', desc: '服务器/HPC' },
  VRT:  { name: '维谛Vertiv', desc: '数据中心供电/液冷' },
  EQIX: { name: 'Equinix', desc: '数据中心REIT' },
  DLR:  { name: 'Digital Realty', desc: '数据中心REIT' },
  AMT:  { name: '美国电塔', desc: '通信基建REIT' },
  PSTG: { name: 'Pure Storage', desc: '闪存存储阵列' },
  NTAP: { name: 'NetApp', desc: '企业存储' },
  CLS:  { name: 'Celestica', desc: '服务器ODM代工' },
  IONQ: { name: 'IonQ', desc: '量子计算' },
  TTMI: { name: 'TTM', desc: '高速PCB' },
  TEL:  { name: 'TE Connectivity', desc: '高速连接器' },
  HPQ:  { name: '惠普', desc: 'PC/打印(边缘)' },

  // ── AI网络/交换 ──
  ANET: { name: 'Arista', desc: '数据中心交换机' },
  CSCO: { name: '思科', desc: '网络设备' },
  CIEN: { name: 'Ciena', desc: '光网络设备' },
  LITE: { name: 'Lumentum', desc: '光模块/激光器' },
  VIAV: { name: 'Viavi', desc: '网络测试/光器件' },
  CALX: { name: 'Calix', desc: '宽带接入' },
  AAOI: { name: 'Applied Opto', desc: '光模块' },
  CRDO: { name: 'Credo', desc: '有源电缆/SerDes' },
  LASR: { name: 'nLight', desc: '光纤/工业激光' },
  NOK:  { name: '诺基亚', desc: '电信网络设备' },
  JBL:  { name: '捷普', desc: '电子制造/光模块' },
  GLW:  { name: '康宁', desc: '光纤/玻璃基板' },
  COHR: { name: 'Coherent', desc: '光通信/激光' },
  SMTC: { name: 'Semtech', desc: '数据中心连接芯片' },
  LPTH: { name: 'LightPath', desc: '红外光学元件' },
  SITM: { name: 'SiTime', desc: '精密时钟芯片' },
  MXL:  { name: 'MaxLinear', desc: '连接/接口芯片' },

  // ── AI算力运营/云 ──
  IREN: { name: 'IREN', desc: '绿电算力数据中心' },
  NBIS: { name: 'Nebius', desc: 'AI GPU云' },
  CRWV: { name: 'CoreWeave', desc: 'GPU云算力' },
  APLD: { name: 'Applied Digital', desc: 'AI数据中心' },
  WULF: { name: 'TeraWulf', desc: '算力/HPC' },
  CORZ: { name: 'Core Scientific', desc: '算力数据中心' },
  BTDR: { name: 'Bitdeer', desc: '矿场转AI算力' },
  HIVE: { name: 'Hive', desc: '算力/HPC' },
  CIFR: { name: 'Cipher', desc: '算力数据中心' },
  POWL: { name: 'Powell', desc: '数据中心配电设备' },
  AGX:  { name: 'Argan', desc: '电力工程EPC' },
  HUT:  { name: 'Hut 8', desc: '算力/HPC' },
  BB:   { name: '黑莓', desc: '软件/QNX(边缘)' },

  // ── 电力/冷却 ──
  VST:  { name: 'Vistra', desc: '电力/核电运营' },
  CEG:  { name: 'Constellation', desc: '核电/清洁电力' },
  ETR:  { name: 'Entergy', desc: '电力公用事业' },
  NRG:  { name: 'NRG Energy', desc: '发电/电力零售' },
  EXC:  { name: 'Exelon', desc: '输配电公用' },
  AES:  { name: 'AES', desc: '全球电力/储能' },
  GEV:  { name: 'GE Vernova', desc: '电力设备/燃机' },
  GTLS: { name: 'Chart', desc: '工业气体/热管理' },
  TDW:  { name: 'Tidewater', desc: '海上能源服务' },
  ETN:  { name: '伊顿', desc: '电力管理设备' },
  GNRC: { name: 'Generac', desc: '备用发电设备' },
  PWR:  { name: 'Quanta', desc: '电网工程建设' },
  FIX:  { name: 'Comfort Sys.', desc: '暖通/机电' },
  EME:  { name: 'EMCOR', desc: '机电工程' },
  OKLO: { name: 'Oklo', desc: '小型核反应堆' },
  LEU:  { name: 'Centrus', desc: '浓缩铀/核燃料' },
  BE:   { name: 'Bloom Energy', desc: '燃料电池供电' },
  FCEL: { name: 'FuelCell', desc: '燃料电池' },
  FLNC: { name: 'Fluence', desc: '储能系统' },
  PSIX: { name: 'Power Solutions', desc: '工业发动机' },
  DGXX: { name: 'Digi Power X', desc: '数据中心配电' },
  MOD:  { name: 'Modine', desc: '数据中心液冷' },
  AAON: { name: 'AAON', desc: '精密空调/冷却' },
  VICR: { name: 'Vicor', desc: '高密度供电模块' },
  TE:   { name: 'T1 Energy', desc: '太阳能/储能' },
}

/** AI 硬件产业链上下游分层（每层含若干子主题组 key，与 ai_universe.json 的 groups 对应）。 */
export const AI_CHAIN_LAYERS: { title: string; flow: string; groups: string[] }[] = [
  { title: '上游 · 设备材料', flow: '造芯片的工具与原料', groups: ['semicon_equip'] },
  { title: '上游 · 芯片',     flow: 'GPU / CPU / 存储',    groups: ['gpu_compute', 'memory_storage'] },
  { title: '中游 · 服务器 / 网络', flow: '组装与互连',     groups: ['datacenter_infra', 'ai_networking'] },
  { title: '下游 · 算力运营 / 云', flow: '把算力变成钱',   groups: ['ai_infra_build', 'hyperscalers'] },
  { title: '配套 · 电力 / 散热',   flow: '能源与冷却贯穿全链', groups: ['power_cooling'] },
]

/**
 * 市值档位（静态，用于产业图谱「龙头高亮」视觉权重；龙头/小盘归属变化慢，无需实时拉）。
 * MEGA：大盘龙头（约 ≥ $100B）→ 卡片高亮 + 👑
 * SMALL：微小盘（约 ≤ $5B）→ 卡片暗淡
 * 其余默认中档样式。归属可按需增删。
 */
export const MEGA_CAPS = new Set<string>([
  'NVDA', 'AVGO', 'TSM', 'AMD', 'QCOM', 'TXN', 'ARM', 'ASML', 'MU', 'ADI',
  'AMAT', 'LRCX', 'KLAC', 'MCHP', 'NXPI', 'INTC', 'MRVL', 'ANET', 'CSCO',
  'AMZN', 'GOOGL', 'IBM', 'ETN', 'CAT', 'GEV', 'VST', 'CEG', 'AMT', 'EQIX',
  'DLR', 'DELL', 'GLW', 'TEL',
])
export const SMALL_CAPS = new Set<string>([
  'AEHR', 'DGXX', 'LPTH', 'AXTI', 'VICR', 'CRDO', 'CALX', 'LASR', 'AGX',
  'PSIX', 'NVEC', 'IPWR', 'MRAM', 'SLNH', 'HYLN', 'NVTS', 'POWI', 'AOSL',
])
