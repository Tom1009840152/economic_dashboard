# 数据来源、口径与更新机制

本文档记录经济学看板所有指标的数据从哪来、怎么清洗、多久刷新一次，方便以后回顾"这个数字是什么意思、能不能信"。代码里也有对应注释（主要在 `backend/app/fetchers/` 三个文件），本文档是这些注释的整理版 + 补充一些代码里没写但需要知道的背景。

## 1. 数据源

| 数据源 | 覆盖范围 | 为什么用它 | 需要 API Key |
| --- | --- | --- | --- |
| **akshare** | 中国股指/汇率/大宗商品/几乎所有中国宏观数据；美国部分宏观数据、中美国债收益率；日本/欧元区 CPI、政策利率、GDP | 免费、聚合了新浪财经/东方财富/国家统计局等多个源，中国数据覆盖最全 | 不需要 |
| **FRED**（美联储圣路易斯分行） | 美国货币供给（M1/M2/货币基础）；日本央行总资产；欧洲央行总资产；韩国外汇储备 | akshare 完全没有这几个指标的接口；FRED 有公开的 CSV 端点，不需要注册/Key，本机网络环境实测可连通，历史数据最早到 1959 年 | 不需要（`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>`） |
| **国家统计局月度数据** | 中国城镇调查失业率、本地/外来户籍劳动力失业率 | 官方劳动力抽样调查，可用于判断就业周期；通过 akshare 的统计局接口封装获取 | 不需要 |
| **World Bank WDI** | 中国、美国、日本、欧盟、韩国的人口规模、增速、年龄结构、生育死亡、迁移、劳动力与实际GDP | API 无需 Key，跨指标年份容易对齐；人口序列底层主要来自联合国人口司 WPP，劳动力序列来自 ILO 模型估计 | 不需要（`https://api.worldbank.org/v2/`） |
| **BLS / FRED** | 美国失业、劳动参与、非农就业、工资、工时和职位空缺 | BLS 是底层官方统计机构，FRED 提供无需 Key 的稳定 CSV 分发接口 | 不需要 |
| **OECD（经 FRED 分发）** | 日本、韩国的15—64岁失业率、就业率、劳动参与率、青年失业和男女参与率 | 月度季调、年龄口径一致，适合跨国比较；不同序列发布时间可能错位 | 不需要 |
| **Eurostat EU-LFS** | 欧盟EU27的失业、青年失业、就业率、参与率、男女就业率和劳动力市场闲置 | 月度与季度混合，使用 `EU27_2020` 聚合口径 | 不需要 |
| **OECD Data Explorer** | 中美日韩综合领先指标；美日韩核心CPI | 直接使用新版SDMX接口，避免FRED中的旧OECD序列停更 | 不需要 |
| **Eurostat STS / HICP** | 欧元区工业生产与核心HICP | 工业生产取EA20季调工作日调整指数并计算同比；核心HICP剔除食品、能源、酒精和烟草 | 不需要 |
| **中国国家统计局发布页** | 中国规上工业增加值同比、核心CPI同比 | 核心CPI不在稳定数据表中，以月度官方解读稿为准；抓取最近滚动窗口并由数据库保留历史 | 不需要 |

FRED 是本项目第一个非 akshare 数据源，后续凡是 akshare 查不到的指标，会优先去 FRED 找有没有免费、还在更新的替代序列，而不是接第三个数据源——保持数据源数量尽量少，方便维护。

对应代码：
- `backend/app/fetchers/akshare_source.py`（股指/汇率/大宗商品）
- `backend/app/fetchers/macro_source.py`（中美日欧的宏观、国债、货币供给——虽然文件名是 macro，但也走 akshare）
- `backend/app/fetchers/fred_source.py`（美国货币供给、日本/欧洲央行总资产、韩国外汇储备）
- `backend/app/fetchers/world_bank_population.py`（五个地区的人口与人口—增长核算专题）
- `backend/app/fetchers/china_employment.py`（中国就业专题；国家统计局月度调查 + WDI/ILO年度结构）
- `backend/app/fetchers/us_employment.py`（美国就业专题；BLS 就业形势报告 + JOLTS，经 FRED 分发）
- `backend/app/fetchers/oecd_employment.py`（日本、韩国就业专题；国家劳动力调查经 OECD/FRED 统一口径）
- `backend/app/fetchers/eu_employment.py`（欧盟就业专题；Eurostat EU-LFS）
- `backend/app/fetchers/oecd_cycle.py`（工业生产、OECD综合领先指标与跨国核心通胀）
- `backend/app/fetchers/nbs_cycle.py`（国家统计局发布页中的中国工业生产与核心CPI）

### 中国人口专题（World Bank WDI）

`GET /api/population/CN` 一次批量取得 1990 年以来的总人口、人口增速、出生率、死亡率、
总和生育率、预期寿命、净迁移、三大年龄组占比、抚养比、城镇化率、劳动参与率、
劳动力总人数与不变价 GDP，并取得男女各 17 个五岁年龄组来生成人口金字塔。

人口是年度慢变量，不跟随市场指标每 6 小时刷新，也不写入单值指标表；接口结果使用 24 小时
内存缓存。页面中的人口增量拆解是近似核算，增长核算使用严格的对数恒等式：
`ln(Y/N) = ln(Y/L) + ln(L/W) + ln(W/N)`。其中 L 是劳动力总人数、W 是 15—64 岁人口；
由于劳动力统计通常覆盖 15 岁以上，L/W 不应误读为官方劳动参与率。

### 中国就业专题（国家统计局 + World Bank WDI/ILO）

`GET /api/employment/CN` 同时返回两类不能互相替代的数据：

- 国家统计局月度城镇调查失业率，以及本地、外来户籍劳动力分组。它是官方抽样调查，适合判断
  月度就业景气，但不覆盖停止求职的非劳动力，也不能单独衡量低工时和低收入就业。
- WDI 中的 ILO 年度模型估计，包括劳动参与率、就业人口比、总体和青年失业率、男女参与率、
  脆弱就业占比。它们是全国 15 岁以上口径，适合长期和国际比较，不作为月度拐点信号。

页面使用同源年度数据展示恒等式
`就业人口比 = 劳动参与率 × (1 - 失业率)`，不把城镇月度失业率与全国年度参与率混算。
2025 年就业总量、城乡就业、农民工和周平均工时来自当年国民经济和社会发展统计公报；由于
统计公报没有稳定的机器接口，这是一项明确标注年份的人工年度快照。

官方青年月度失业率自 2024 年起改为“不含在校生”的 16—24、25—29、30—59 岁分组，
与此前口径存在断点。当前免费接口不能稳定提供这组完整历史序列，因此第一版只展示明确标注为
ILO 模型估计的 15—24 岁年度序列，不伪造或拼接官方月度数据。

### 美国人口与就业专题（World Bank WDI + BLS/FRED）

`GET /api/population/US` 与中国人口专题采用同一批 WDI 指标和五岁年龄组，因此人口增长、
年龄结构、金字塔和增长核算可以跨国比较。页面仍逐项显示年份，不把不同更新时间的指标视为
同一时点。

`GET /api/employment/US` 从 FRED 的免 Key CSV 接口获取 BLS 官方序列：

- 家庭调查（CPS）：U-3 失业率、U-6 劳动利用不足率、16—24 岁失业率、劳动参与率、
  就业人口比和失业人数。
- 企业调查（CES）：非农就业人数、非农就业月度变化、私营部门平均工时、平均时薪及同比。
- JOLTS：非农职位空缺，并与失业人数对齐计算 `职位空缺 / 失业人数`。

家庭调查按人统计并包含自雇者，企业调查按工资单岗位统计且不含农场和非注册自雇；两者短期
背离并非数据错误。页面使用同源家庭调查展示
`就业人口比 = 劳动参与率 × (1 - U-3失业率)`，并明确提示非农就业和职位空缺会修订。

### 日本、欧盟、韩国人口与就业专题

`GET /api/population/JP|EU|KR` 与中美人口专题采用同一组 WDI 指标和五岁年龄组。欧盟使用
世界银行 `EUU` 聚合地区代码；人口专题中的增长分解仍是
`ln(Y/N) = ln(Y/L) + ln(L/W) + ln(W/N)`，用于定位人均增长来自生产率、劳动力利用还是年龄结构。

`GET /api/employment/JP` 与 `GET /api/employment/KR` 使用 OECD Infra-Annual Labour
Statistics 的月度季调序列，并统一为15—64岁口径。除总体和青年失业率外，还展示就业率、
劳动参与率、男女参与率及其差距。页面以
`隐含失业率 = 1 - 就业率 / 劳动参与率` 核对同年龄口径指标。

`GET /api/employment/EU` 直接读取 Eurostat：`une_rt_m` 提供 EU27_2020 月度失业率，
`lfsi_emp_q` 提供20—64岁季度就业率和参与率，`lfsi_sla_q` 提供更宽口径的劳动力市场闲置率。
由于头条失业率覆盖15—74岁，而就业率/参与率覆盖20—64岁，恒等式隐含值只用于结构核对，
不会与头条失业率机械混算；同时明确提示 EU27 总量可能遮蔽成员国分化。

## 2. 指标清单（按 region 分组）

### 股指 / 汇率 / 大宗商品（全球通用，akshare）

| code | 名称 | 数据源接口 | 口径备注 |
| --- | --- | --- | --- |
| SSE | 上证指数 | `ak.stock_zh_index_daily` | 新浪接口，1990-12-19 起 |
| DJI | 道琼斯工业指数 | `ak.index_us_stock_sina` | 新浪美股接口，2004-01-02 起 |
| NKY | 日经225 | `ak.index_global_hist_sina` | 新浪接口，只保留近约4年数据（源本身限制） |
| STOXX50 | 欧洲斯托克50 | `ak.index_global_hist_sina` | 同上，近约4年 |
| KOSPI | 韩国综合指数 | `ak.index_global_hist_sina` | 同上，近约4年 |
| GOLD | COMEX黄金 | `ak.futures_foreign_hist(GC)` | 新浪外盘期货，2016-09-06 起 |
| WTI | WTI原油 | `ak.futures_foreign_hist(CL)` | 新浪外盘期货，1996-09-06 起 |
| USDCNY/EURCNY/JPYCNY/GBPCNY/CADCNY/AUDCNY/KRWCNY/CHFCNY/SEKCNY/THBCNY/SGDCNY/NOKCNY | 12 个货币对人民币 | `ak.currency_boc_sina`（中国银行外汇牌价） | 见下方"汇率换算"专节 |

汇率的 12 个货币是按名义 GDP 排名，从中行牌价能提供的币种里选的（中行牌价覆盖：美元/英镑/欧元/澳门元/泰铢/菲律宾比索/港币/瑞士法郎/新加坡元/丹麦克朗/挪威克朗/日元/加元/澳元/新西兰元/韩元）。覆盖不到印度卢比、巴西雷亚尔、俄罗斯卢布——这几个 GDP 靠前但中行不提供报价；港币/澳门元因为是地区不是主权国家，没有选用。

### 中国宏观（akshare，国家统计局/央行数据）

| code | 名称 | 数据源接口 | 口径备注 |
| --- | --- | --- | --- |
| CN_CPI | CPI同比 | `ak.macro_china_cpi` | 月度，取"全国-同比增长" |
| CN_PPI | PPI同比 | `ak.macro_china_ppi` | 月度，取"当月同比增长" |
| CN_PMI | 制造业PMI | `ak.macro_china_pmi` | 月度，取"制造业-指数" |
| CN_NMI | 非制造业商务活动指数 | `ak.macro_china_pmi` | 月度，取"非制造业-指数"；50为荣枯线 |
| CN_IP | 规上工业增加值同比 | 国家统计局数据发布页 | 月度可比价同比，不等于全部工业企业产出 |
| CN_CLI | 综合领先指标 | OECD Data Explorer `DF_CLI` | 月度，振幅调整，长期均值=100 |
| CN_CORE_CPI | 核心CPI同比 | 国家统计局月度CPI/PPI解读稿 | 剔除食品和能源；官方发布值，不自行估算权重 |
| CN_TSF | 社会融资规模增量 | `ak.macro_china_shrzgm` | 月度，YYYYMM 格式日期 |
| CN_GDP | GDP同比 | `ak.macro_china_gdp_yearly` | 季度 |
| CN_RETAIL | 社会消费品零售总额同比 | `ak.macro_china_consumer_goods_retail` | 月度 |
| CN_FAI | 固定资产投资同比 | `ak.macro_china_gdzctz` | 月度 |
| CN_EXPORTS | 出口同比 | `ak.macro_china_exports_yoy` | 日频发布节奏，海关总署 |
| CN_EXPORTS_ABS | 出口额（伴生指标，不单独出卡片） | `ak.macro_china_hgjck` | 原始单位"千美元"，除以 1e5 换算成"亿美元" |
| CN_HOG | 生猪现货价格指数 | `ak.index_hog_spot_price` | 周频，判断"猪周期"最常用的原始价格序列 |
| CN_REALESTATE | 国房景气指数 | `ak.macro_china_real_estate` | 全国综合房地产市场冷热度 |
| CN_ENERGY | 能源指数 | `ak.macro_china_energy_index` | |
| CN_2Y/5Y/10Y/30Y | 国债收益率 | `ak.bond_zh_us_rate` | 见下方"国债收益率数据清洗"专节 |
| CN_10Y2Y | 10年-2年利差 | 同上接口直接取列 | 经典衰退先行信号 |
| CN_M0/M1/M2（各带 _ABS/_YOY/_MOM） | 货币供给 | `ak.macro_china_money_supply` | 四个指标（M0/M1/M2/剪刀差）共用一次接口调用，见下方"货币供给"专节 |
| CN_M1M2 | M1-M2剪刀差 | 同上，`M1同比 - M2同比` 现场计算 | 反映企业资金活化程度 |

### 美国宏观（akshare + FRED 混用）

| code | 名称 | 数据源 | 口径备注 |
| --- | --- | --- | --- |
| US_CPI | CPI同比 | akshare `ak.macro_usa_cpi_yoy` | |
| US_CORE_CPI | 核心CPI同比 | OECD Data Explorer | 剔除食品和能源，同比 |
| US_IP | 工业生产同比 | FRED `INDPRO` | 月度季调指数计算同比 |
| US_CLI | 综合领先指标 | OECD Data Explorer `DF_CLI` | 振幅调整，长期均值=100 |
| US_NFP | 非农就业变动 | akshare `ak.macro_usa_non_farm` | 单位：万人 |
| US_FFR | 联邦基金利率 | akshare `ak.macro_bank_usa_interest_rate` | |
| US_GDP | GDP环比折年率 | akshare `ak.macro_usa_gdp_monthly` | |
| US_2Y/5Y/10Y/30Y、US_10Y2Y | 国债收益率+利差 | akshare `ak.bond_zh_us_rate`（和中国共用同一个接口，分不同列） | |
| US_BASE_ABS/YOY/MOM | 货币基础（顶替"美国M0"） | **FRED** `BOGMBASE` | 见下方"美国货币供给"专节 |
| US_M1_ABS/YOY/MOM | M1 | **FRED** `M1SL` | 2020-05 有统计口径断层，见专节 |
| US_M2_ABS/YOY/MOM | M2 | **FRED** `M2SL` | |

### 日本 / 欧元区 / 韩国

| code | 名称 | 数据源 | 口径备注 |
| --- | --- | --- | --- |
| JP_CPI | 日本CPI同比 | akshare `ak.macro_japan_cpi_yearly` | |
| JP_CORE_CPI | 日本核心CPI同比 | OECD Data Explorer（COICOP 2018） | 剔除食品和能源，同比 |
| JP_IP | 日本工业生产同比 | OECD经FRED `JPNPRINTO01GYSAM` | 月度季调，同比 |
| JP_CLI | 日本综合领先指标 | OECD Data Explorer `DF_CLI` | 振幅调整，长期均值=100 |
| JP_BOJ | 日本央行政策利率 | akshare `ak.macro_bank_japan_interest_rate` | |
| JP_BOJ_ASSETS | 日本央行总资产 | **FRED** `JPNASSETS` | M1/M2 替代指标，见专节 |
| EU_CPI | 欧元区CPI同比（HICP） | akshare `ak.macro_euro_cpi_yoy` | |
| EU_CORE_CPI | 欧元区核心HICP同比 | Eurostat经FRED `00XEFDEZ19M086NEST` | 剔除食品、能源、酒精和烟草，指数计算同比 |
| EU_IP | 欧元区工业生产同比 | Eurostat `sts_inpr_m` | EA20、季调及工作日调整，指数计算同比 |
| EU_CLI | 欧洲四大经济体领先指标（代理） | OECD Data Explorer `DF_CLI`，`G4E` | OECD无当前欧元区CLI；代理包含英国，界面明确标注 |
| EU_ECB | 欧洲央行利率 | akshare `ak.macro_bank_euro_interest_rate` | |
| EU_ECB_ASSETS | 欧洲央行总资产 | **FRED** `ECBASSETSW` | M1/M2 替代指标，见专节 |
| EU_GDP | 欧元区GDP同比 | akshare `ak.macro_euro_gdp_yoy` | |
| KOSPI | 韩国综合指数 | akshare（见上表） | |
| KRWCNY | 韩元/人民币 | akshare（见上表） | |
| KR_RESERVES | 韩国外汇储备 | **FRED** `TRESEGKRM052N` | **不是货币供给替代指标**，见专节 |
| KR_CORE_CPI | 韩国核心CPI同比 | OECD Data Explorer | 剔除食品和能源，同比 |
| KR_IP | 韩国工业生产同比 | OECD经FRED `KORPRINTO01GYSAM` | 月度季调，同比 |
| KR_CLI | 韩国综合领先指标 | OECD Data Explorer `DF_CLI` | 振幅调整，长期均值=100 |

**日本 GDP、韩国政策利率/GDP 目前仍是空白**——现有免费接口没有找到稳定且持续更新的序列；韩国核心CPI已通过OECD补齐，但不能代替完整的整体CPI。

## 3. 汇率换算逻辑

所有货币对的数据入库时，统一存成"1外币 = X 人民币"。前端任意两个货币互相换算（`GET /api/forex/rate`），用人民币做换算枢纽：`1 base = (base对CNY的价格) / (target对CNY的价格) target`，不用给每一对货币单独维护数据。代码见 `backend/app/services/forex_service.py`。

**日元、韩元的特殊换算**：中行牌价里日元、韩元习惯按"每100单位"报价（比如"100日元=X人民币"），其它货币按"每1单位"报价。入库时統一按 `BOC_CURRENCIES` 里记录的 scale 处理，但换算成"1单位=X人民币"时要再除以100，否则和其它货币直接相除会差100倍——这是本项目早期出现过的一个真实 bug（表现为"1人民币=0.23日元"这种明显不对的数字），修复后的逻辑固化在 `_to_cny_series()` 的 `per_unit_factor = 1 / (ingest_scale * 100)` 里。

## 4. 国债收益率数据清洗：中国2年/30年期的起始日期

中国2年期、30年期国债收益率在早期（约2002-2005年）数据里有明显的脏数据——单日能跳1~3个百分点、然后立刻跳回去，排查后确认是当时这两个期限交易极不活跃，数据源构建收益率曲线时反复"跳变"，不是真实行情、也不是疫情之类的经济事件导致。处理方式是给这两个期限单独设置一个更晚的可信起始日期（而不是用全局的 `DATA_FLOOR_DATE = 2000-01-01`）：

- `CN_2Y`：2003-05-01 起
- `CN_30Y`：2005-03-01 起
- `CN_10Y2Y`（利差）：因为用到2年期，同样从 2003-05-01 起

这个 floor 日期只过滤未来重新抓取的数据；发现问题时数据库里已经存在的脏数据是额外手动 DELETE 掉的，仅改 fetcher 代码不会追溯清理已入库的历史行。

## 5. 中国货币供给（M0/M1/M2）

数据源：`ak.macro_china_money_supply()`，一次调用能拿到 M0/M1/M2 的数量（亿元）、同比、环比，所以 `CN_M0_ABS/YOY/MOM`、`CN_M1_*`、`CN_M2_*`、`CN_M1M2`（M1-M2剪刀差，两个同比相减现场算）这10个指标共用同一份原始数据，用60秒 TTL 内存缓存合并成一次网络请求，避免每次刷新发10次重复请求（`_raw_money_supply_df()`）。

前端把这10个指标合并展示成一张"货币供给"汇总卡：点进去顶部切换 M0/M1/M2 + 剪刀差，图内切换绝对值/同比/环比，经济学解读文案在切换 M0/M1/M2 时不换（因为讲的是同一套传导机制），只在切到剪刀差 tab 时换成剪刀差专属的解读。

## 6. 美国货币供给：为什么用"货币基础"顶替 M0，以及 2020 年的统计口径断层

美国没有官方常规发布的"流通中现金 M0"这个概念，最接近的是**货币基础**（Monetary Base = 流通中现金 + 银行在美联储的准备金，FRED 序列 `BOGMBASE`），概念上比中国 M0 略宽（多了银行准备金部分），本项目用它顶替"美国 M0"卡片位置，前端经济学解读词条里会说明这个概念差异，不会含糊过去。

**2020年5月 M1 重新定义**：美联储把储蓄存款（此前只算在 M2 里）也纳入了 M1 统计口径，导致 `M1SL` 原始数据在 2020-04 到 2020-05 之间出现一次巨大跳变——这是统计口径变化造成的，不是真实的货币供给暴增。本项目如实保留原始数据（不删除、不做特殊处理），但在前端词条里明确解释这次断层的成因，避免被误读成"疫情放水导致M1暴涨"（QE确实是2020年的重要背景，但这次特定的跳变主要是口径切换造成的，两者要分开看）。

也没有对应中国"M1-M2剪刀差"的惯用美国指标，所以美国货币供给页面没有剪刀差 tab。

## 7. 日本 / 欧元区 / 韩国：M1、M2 数据为什么缺失，以及各自的替代方案

这三个经济体都遇到同一个问题：FRED 上能查到对应的 M1/M2 序列（数据源是 OECD 转载），但都已经停止更新，放进一个要持续刷新的看板里没有意义：

| 经济体 | M1 序列 | 最后更新 | M2 序列 | 最后更新 |
| --- | --- | --- | --- | --- |
| 日本 | `MANMM101JPM189S` | 2023-11（死） | `MANMM102JPM189S` | 2017-02（死） |
| 欧元区 | `MANMM101EZM189S` | 2023-11（死） | `MYAGM2EZM189S` | 查不到（404） |
| 韩国 | `MANMM101KRM189S` | 2023-10（死） | `MYAGM2KRM189S` | 2017-05（死） |

针对这个共同问题，日本和欧元区找到了同一种思路的替代方案，韩国则完全没有：

- **日本**：用日本央行**总资产**（`JPNASSETS`，亿日元）替代。日本自1990年代资产泡沫破裂后长期处于"流动性陷阱"/"资产负债表衰退"，传统的"利率→信贷→存款派生→M1/M2"传导链条基本失效，日本央行的实际发力方式是直接下场买国债/ETF（QQE）+ 收益率曲线控制（YCC），这种情况下央行资产负债表的扩张速度本身，比 M1/M2 更能反映货币政策的实际力度。
- **欧元区**：用欧洲央行**总资产**（`ECBASSETSW`，周度金融报表口径，百万欧元）替代，原理和日本一致。月度口径的 `ECBASSETS` 也已停更（停在2020-01），只有周度口径还在正常更新（每周五）。欧央行资产负债表 2015-2022 年经历持续扩张（APP量化宽松 + 疫情后PEPP + TLTRO），2022年后转向加息+缩表（QT），目前仍处于收缩趋势中。
- **韩国**：情况比前两者更差，连"央行总资产"类的替代指标都没找到能持续更新的——FRED 上唯一能查到的"央行总资产/GDP"（`DDDI06KRA156NWDB`）是年度数据且停在2021年，同样是死数据。多次搜索（FRED 站内搜索 + tag 浏览）后确认没有更好的选项，最终用**外汇储备**（`TRESEGKRM052N`，Reserves Excluding Gold，百万美元，月度，持续更新）顶替。**这是一个明确的让步，不是严格意义上的"货币供给"替代指标**——外汇储备反映的是央行持有的外币资产规模，概念上更接近"应对汇率波动的缓冲垫"，和衡量国内货币供给/央行扩表的 M1/M2/总资产是两码事。前端词条会明确写清楚这一点，不会包装成"韩国的M0/M1/M2"来误导。

## 8. 更新机制

- **启动时**：`ensure_indicators_seeded()` 把 `indicator_defs.py` 里定义的指标 upsert 进 `indicators` 表（新增/改名/改单位都靠这一步同步），然后立刻在后台线程跑一次全量刷新，不阻塞 API 启动。
- **定时刷新**：APScheduler 每 **6 小时**跑一次全量刷新（`backend/app/scheduler.py`），覆盖全部指标。
- **手动刷新**：`POST /api/refresh` 可以随时手动触发一次全量刷新，调试或者急着看最新数据时用。
- **每个指标独立容错**：`fetch_all()` 逐个指标调用对应 fetcher，单个指标失败（网络问题、接口格式变化等）只记日志、不影响其它指标继续刷新。
- **同批次共享接口调用的短 TTL 缓存**：中国货币供给（10个指标）、中美国债收益率（10个指标）、每个 FRED 序列的 ABS/YOY/MOM（3个指标共享1次请求）都各自用了60秒 TTL 的内存缓存，把同一次刷新里对同一个底层接口的多次调用合并成一次网络请求，避免刷新一次发几十个重复请求。
- 写入用 upsert（按 `indicator_code + date` 唯一约束），重复刷新同一天的数据不会产生重复行，只会更新数值。

## 9. 已知限制

- `Indicator.source` 这个数据库字段目前**没有被实际维护**——`indicator_defs.py` 里没有指定过，永远是模型定义的默认值 `"akshare"`，即便实际数据来自 FRED（美国货币供给、日本/欧洲央行总资产、韩国外汇储备）。这个字段目前前后端都没有读取展示，暂时只是列在这里防止以后查库时被这个默认值误导；真正的数据源以本文档和代码注释为准。
- 各指标的历史长度上限完全取决于免费数据源本身能追溯到多早（比如道琼斯只能到2004年、COMEX黄金只能到2016年），不是本项目主动截断的。
- 月度宏观指标（CPI/PPI/PMI等）从统计局/央行公布到接口能查到，通常有 1-2 个月的自然滞后，这是数据发布节奏本身的性质。
