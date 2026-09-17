export type SourceKind = "official" | "international" | "distributor" | "derived";

export type DataSourceRegion = "cn" | "us" | "jp" | "eu" | "uk" | "kr" | "global";

export interface DataSourceRegionMeta {
  slug: DataSourceRegion;
  apiRegion: "CN" | "US" | "JP" | "EU" | "GB" | "KR" | "GLOBAL";
  label: string;
  description: string;
}

export const DATA_SOURCE_REGIONS: DataSourceRegionMeta[] = [
  {
    slug: "cn",
    apiRegion: "CN",
    label: "中国",
    description: "宏观、市场、人口、就业以及中国综合分析模型所使用的全部数据。",
  },
  {
    slug: "us",
    apiRegion: "US",
    label: "美国",
    description: "美国增长、通胀、就业、货币、利率、市场、人口及综合分析使用的全部数据。",
  },
  {
    slug: "jp",
    apiRegion: "JP",
    label: "日本",
    description: "日本物价、生产、领先指标、日本银行利率与资产负债表、就业、人口、市场及综合分析的全部输入；当前缺少可用GDP与完整金融条件数据。",
  },
  {
    slug: "eu",
    apiRegion: "EU",
    label: "欧元区",
    description: "当前欧元区 EA21 的增长、通胀、就业、货币政策、人口、市场及综合分析使用的全部数据；不与欧盟 EU27 混用。",
  },
  {
    slug: "uk",
    apiRegion: "GB",
    label: "英国",
    description: "英国增长、通胀、就业、货币政策、广义货币、人口、市场及综合分析使用的全部数据；不与欧元区或欧盟聚合值混用。",
  },
  {
    slug: "kr",
    apiRegion: "KR",
    label: "韩国",
    description: "韩国核心通胀、生产、领先指标、就业、人口、韩国银行官方基准利率、外汇储备、市场及综合分析的全部输入；当前缺少总体CPI与GDP。",
  },
  {
    slug: "global",
    apiRegion: "GLOBAL",
    label: "全球与跨市场",
    description: "不归属于单一经济体的汇率、黄金、原油与跨市场数据。",
  },
];

export const DATA_SOURCE_REGION_BY_SLUG = new Map(
  DATA_SOURCE_REGIONS.map((region) => [region.slug, region]),
);

export interface SourceRegistryEntry {
  key: string;
  label: string;
  institution: string;
  kind: SourceKind;
  url?: string;
  access: string;
  description: string;
  caveat?: string;
}

export const SOURCE_REGISTRY: SourceRegistryEntry[] = [
  {
    key: "NBS",
    label: "国家统计局",
    institution: "中华人民共和国国家统计局",
    kind: "official",
    url: "https://www.stats.gov.cn/sj/",
    access: "官方发布页、国家数据接口与公告表格",
    description: "覆盖物价、PMI、GDP、生产、消费、投资、工业企业与房地产等中国宏观数据。",
    caveat: "不同指标的发布日期不同；月度指标也可能按累计口径发布。",
  },
  {
    key: "PBOC",
    label: "中国人民银行",
    institution: "中国人民银行",
    kind: "official",
    url: "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html",
    access: "官方统计栏目、月度公告与附件",
    description: "覆盖货币供应、社会融资规模、贷款与债券融资等货币信用数据。",
    caveat: "M1 自 2025 年起使用新定义；项目对 2024 年采用官方可比回溯值。",
  },
  {
    key: "MOF",
    label: "财政部",
    institution: "中华人民共和国财政部",
    kind: "official",
    url: "https://gks.mof.gov.cn/tongjishuju/",
    access: "官方财政收支与地方债统计公告",
    description: "覆盖一般公共预算、政府性基金预算支出及新增地方专项债发行。",
    caveat: "财政数据多为年内累计值；项目在季度末与同期累计名义 GDP 对齐。",
  },
  {
    key: "GACC",
    label: "海关总署",
    institution: "中华人民共和国海关总署",
    kind: "official",
    url: "https://www.customs.gov.cn/",
    access: "官方统计快讯与月度发布页",
    description: "覆盖中国出口额和出口同比，用于外需与贸易动能判断。",
    caveat: "单月值和累计值不可混用；项目按公告明确的观察期入库。",
  },
  {
    key: "Bank of China",
    label: "中国银行外汇牌价",
    institution: "中国银行",
    kind: "official",
    url: "https://www.boc.cn/sourcedb/whpj/",
    access: "银行公开外汇牌价",
    description: "覆盖主要货币兑人民币牌价，供汇率卡片与换算器使用。",
    caveat: "这是银行牌价而非境内即期市场收盘价；日元、韩元按 100 单位报价。",
  },
  {
    key: "Bank of England",
    label: "英格兰银行",
    institution: "Bank of England",
    kind: "official",
    url: "https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp",
    access: "官方 Bank Rate 数据库",
    description: "覆盖英国 Bank Rate，按英格兰银行货币政策委员会决议变更，并用于英国综合分析的政策方向判断。",
    caveat: "Bank Rate 是事件触发序列；展示值必须结合生效日和核验日期，不能把旧值当作实时政策状态。",
  },
  {
    key: "Bank of Korea",
    label: "韩国银行",
    institution: "Bank of Korea",
    kind: "official",
    url: "https://www.bok.or.kr/eng/singl/baseRate/progress.do?dataSeCd=01&menuNo=400016",
    access: "官方基准利率历史页（事件变更记录）",
    description: "覆盖韩国银行基准利率的官方事件历史，按利率决议生效日用于韩国综合分析的政策方向判断。",
    caveat: "生效日与最近核验日含义不同：后者仅说明何时确认当前值，不代表发生新调息。事后实际利率以核心CPI近似，总体CPI缺失时不应解读为完整的实际政策利率。",
  },
  {
    key: "UK ONS",
    label: "英国国家统计局",
    institution: "Office for National Statistics",
    kind: "official",
    url: "https://www.ons.gov.uk/",
    access: "官方数据集下载",
    description: "覆盖英国通胀、工业生产、GDP与劳动力市场序列。",
    caveat: "ONS 宏观和劳动力调查数据会修订；项目展示当前可见修订值，不保留每次初值发布的完整历史版本。",
  },
  {
    key: "Eurostat/EC",
    label: "欧盟统计局 / 欧委会",
    institution: "Eurostat / European Commission",
    kind: "international",
    url: "https://ec.europa.eu/eurostat/databrowser/",
    access: "官方统计 API 与数据浏览器",
    description: "覆盖欧元区 EA21 的工业生产、GDP、就业、劳动力闲置，以及欧委会经济景气指标。",
    caveat: "EA21 是欧元区聚合口径，并非欧盟 EU27；聚合值也会掩盖成员国之间的增长、通胀与就业分化。",
  },
  {
    key: "OECD",
    label: "OECD",
    institution: "Organisation for Economic Co-operation and Development",
    kind: "international",
    url: "https://data-explorer.oecd.org/",
    access: "OECD SDMX API",
    description: "覆盖多个经济体的综合领先指标及部分核心通胀序列。",
    caveat: "国际数据库可能晚于各国首次发布，并会随来源修订历史值。",
  },
  {
    key: "FRED",
    label: "FRED",
    institution: "Federal Reserve Bank of St. Louis",
    kind: "international",
    url: "https://fred.stlouisfed.org/",
    access: "FRED 官方 CSV 分发接口",
    description: "分发美联储、统计机构和各国央行序列，覆盖利率、产出、货币和央行资产。",
    caveat: "FRED 是可信分发渠道，但具体口径归属原始发布机构。",
  },
  {
    key: "Eastmoney",
    label: "东方财富数据中心",
    institution: "东方财富",
    kind: "distributor",
    url: "https://data.eastmoney.com/cjsj/",
    access: "公开数据接口与专题页",
    description: "补充国债收益率、景气、消费者信心及 70 城房价等序列。",
    caveat: "属于分发渠道；分析时结合观测级来源链接核验原始机构口径。",
  },
  {
    key: "Sina Finance",
    label: "新浪财经行情",
    institution: "新浪财经",
    kind: "distributor",
    url: "https://finance.sina.com.cn/",
    access: "公开市场行情接口",
    description: "覆盖主要股票指数、黄金与原油日度行情。",
    caveat: "行情是展示与分析用分发数据，不替代交易所结算或授权行情。",
  },
  {
    key: "Jin10/AkShare",
    label: "金十数据 / AkShare",
    institution: "金十数据，经 AkShare 接入",
    kind: "distributor",
    url: "https://akshare.akfamily.xyz/",
    access: "AkShare 公共接口",
    description: "用于补充美国与日本 CPI 发布序列。",
    caveat: "属于第三方分发路径，页面展示的来源名不等于原始统计机构。",
  },
  {
    key: "Hangqingbao",
    label: "行情宝",
    institution: "行情宝",
    kind: "distributor",
    url: "https://hqb.nxin.com/",
    access: "公开行业行情",
    description: "提供中国生猪现货价格指数周度序列。",
  },
  {
    key: "Derived",
    label: "项目派生指标",
    institution: "本项目版本化计算",
    kind: "derived",
    access: "由已入库原始序列按公开公式计算",
    description: "包括 M1−M2、信用强度、信用脉冲、财政代理，以及美国、欧元区、英国、日本和韩国综合分析中的透明阈值与证据广度。",
    caveat: "派生值不是官方发布，也不是衰退概率；页面同时披露规则、输入指标、观察期和方法版本。",
  },
];

export const SOURCE_REGISTRY_BY_KEY = new Map(
  SOURCE_REGISTRY.map((entry) => [entry.key, entry]),
);

export const FREQUENCY_LABELS: Record<string, string> = {
  daily: "日度",
  weekly: "周度",
  monthly: "月度",
  quarterly: "季度",
  event: "事件触发",
};

export const REGION_LABELS: Record<string, string> = {
  CN: "中国",
  US: "美国",
  JP: "日本",
  EU: "欧元区",
  GB: "英国",
  KR: "韩国",
  GLOBAL: "全球",
};
