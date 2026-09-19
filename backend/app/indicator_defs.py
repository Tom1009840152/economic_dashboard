"""指标元信息定义，category 取值：index / forex / commodity / macro / bond / money。
region 取值：CN / US / JP / EU / GB / KR / GLOBAL，其中 EU 专指欧元区、GB 专指英国；跨国的汇率/大宗商品
（除了直接代表某国/地区货币的那几个）归为 GLOBAL，只在"综合"页和汇率换算器里出现。

bond 类是国债收益率，同一国家会有多个期限（2Y/5Y/10Y/30Y）+ 10年-2年利差，
前端会把同一国家的 bond 指标合并成一张汇总卡，点进去用多线图一起看，
不像其它指标那样每个单独一张卡——10年-2年利差是经典的衰退先行信号，值得多个期限放在一起看。

韩国已补充OECD核心CPI、工业生产、CLI、外汇储备和韩国银行官方基准利率事件历史，
但总体CPI与GDP仍缺少稳定的持续更新接口；日本GDP同样缺失。缺口会在综合分析里保持为空，
不用代理值代填。

sort_order 决定看板展示顺序：index/commodity 按品类分组在前后，
forex 组内按各经济体名义 GDP 排名排序（数据源为中国银行外汇牌价，
覆盖不到印度、巴西、俄罗斯等 GDP 靠前但该接口不提供报价的国家；
港币/澳门元因为是地区而非主权国家不选用），
macro/bond 组是月度/日度的宏观经济数据，中国的在前、美国、日本、欧元区、英国、韩国依次在后。
"""

from app.indicator_catalog import enrich_indicator_defs


INDICATOR_DEFS = [
    {"code": "SSE", "name": "上证指数", "category": "index", "unit": "点", "sort_order": 0, "region": "CN"},
    {"code": "DJI", "name": "道琼斯工业指数", "category": "index", "unit": "点", "sort_order": 1, "region": "US"},
    {"code": "NKY", "name": "日经225指数", "category": "index", "unit": "点", "sort_order": 2, "region": "JP"},
    {"code": "STOXX50", "name": "欧洲斯托克50指数", "category": "index", "unit": "点", "sort_order": 3, "region": "EU"},
    {"code": "FTSE100", "name": "英国富时100指数", "category": "index", "unit": "点", "sort_order": 4, "region": "GB"},
    {"code": "KOSPI", "name": "韩国综合指数", "category": "index", "unit": "点", "sort_order": 5, "region": "KR"},
    {"code": "USDCNY", "name": "美元/人民币", "category": "forex", "unit": "CNY", "sort_order": 10, "region": "US"},
    {"code": "EURCNY", "name": "欧元/人民币", "category": "forex", "unit": "CNY", "sort_order": 11, "region": "EU"},
    {"code": "JPYCNY", "name": "日元/人民币", "category": "forex", "unit": "CNY/100日元", "sort_order": 12, "region": "JP"},
    {"code": "GBPCNY", "name": "英镑/人民币", "category": "forex", "unit": "CNY", "sort_order": 13, "region": "GB"},
    {"code": "CADCNY", "name": "加元/人民币", "category": "forex", "unit": "CNY", "sort_order": 14, "region": "GLOBAL"},
    {"code": "AUDCNY", "name": "澳元/人民币", "category": "forex", "unit": "CNY", "sort_order": 15, "region": "GLOBAL"},
    {"code": "KRWCNY", "name": "韩元/人民币", "category": "forex", "unit": "CNY/100韩元", "sort_order": 16, "region": "KR"},
    {"code": "CHFCNY", "name": "瑞士法郎/人民币", "category": "forex", "unit": "CNY", "sort_order": 17, "region": "GLOBAL"},
    {"code": "SEKCNY", "name": "瑞典克朗/人民币", "category": "forex", "unit": "CNY", "sort_order": 18, "region": "GLOBAL"},
    {"code": "THBCNY", "name": "泰铢/人民币", "category": "forex", "unit": "CNY", "sort_order": 19, "region": "GLOBAL"},
    {"code": "SGDCNY", "name": "新加坡元/人民币", "category": "forex", "unit": "CNY", "sort_order": 20, "region": "GLOBAL"},
    {"code": "NOKCNY", "name": "挪威克朗/人民币", "category": "forex", "unit": "CNY", "sort_order": 21, "region": "GLOBAL"},
    {"code": "GOLD", "name": "COMEX黄金", "category": "commodity", "unit": "美元/盎司", "sort_order": 30, "region": "GLOBAL"},
    {"code": "WTI", "name": "WTI原油", "category": "commodity", "unit": "美元/桶", "sort_order": 31, "region": "GLOBAL"},
    {"code": "CN_CPI", "name": "中国CPI同比", "category": "macro", "unit": "%", "sort_order": 40, "region": "CN"},
    {"code": "CN_CORE_CPI", "name": "中国核心CPI同比", "category": "macro", "unit": "%", "sort_order": 41, "region": "CN"},
    {"code": "CN_PPI", "name": "中国PPI同比", "category": "macro", "unit": "%", "sort_order": 42, "region": "CN"},
    {"code": "CN_PMI", "name": "中国制造业PMI", "category": "macro", "unit": "点", "sort_order": 43, "region": "CN"},
    {"code": "CN_NMI", "name": "中国非制造业商务活动指数", "category": "macro", "unit": "点", "sort_order": 44, "region": "CN"},
    {"code": "CN_IP", "name": "中国规上工业增加值同比", "category": "macro", "unit": "%", "sort_order": 45, "region": "CN"},
    {"code": "CN_CLI", "name": "中国综合领先指标", "category": "macro", "unit": "点", "sort_order": 46, "region": "CN"},
    {"code": "CN_TSF", "name": "中国社会融资规模增量", "category": "macro", "unit": "亿元", "sort_order": 47, "region": "CN"},
    {"code": "CN_GDP", "name": "中国GDP累计同比", "category": "macro", "unit": "%", "sort_order": 48, "region": "CN"},
    {"code": "CN_RETAIL", "name": "中国社会消费品零售总额同比", "category": "macro", "unit": "%", "sort_order": 49, "region": "CN"},
    {"code": "CN_FAI", "name": "中国固定资产投资同比", "category": "macro", "unit": "%", "sort_order": 50, "region": "CN"},
    {"code": "CN_EXPORTS", "name": "中国出口同比", "category": "macro", "unit": "%", "sort_order": 51, "region": "CN"},
    # 前端把这个当作 CN_EXPORTS 的"绝对水平"伴生指标显示在同一个详情页里，不在卡片网格里单独出现，sort_order 无实际意义
    {"code": "CN_EXPORTS_ABS", "name": "中国出口额", "category": "macro", "unit": "亿美元", "sort_order": 51, "region": "CN"},
    {"code": "CN_HOG", "name": "中国生猪现货价格指数", "category": "macro", "unit": "点", "sort_order": 52, "region": "CN"},
    # money 类：M0/M1/M2 各自有绝对值/同比/环比三种口径，前端合并成一张"货币供给"汇总卡，
    # 点进去顶部切换 M0/M1/M2，图表内切换绝对值/同比/环比，不在卡片网格里逐个铺开
    {"code": "CN_M2_ABS", "name": "中国M2数量", "category": "money", "unit": "亿元", "sort_order": 53, "region": "CN"},
    {"code": "CN_M2_YOY", "name": "中国M2同比", "category": "money", "unit": "%", "sort_order": 53, "region": "CN"},
    {"code": "CN_M2_MOM", "name": "中国M2环比", "category": "money", "unit": "%", "sort_order": 53, "region": "CN"},
    {"code": "CN_M1_ABS", "name": "中国M1数量", "category": "money", "unit": "亿元", "sort_order": 54, "region": "CN"},
    {"code": "CN_M1_YOY", "name": "中国M1同比", "category": "money", "unit": "%", "sort_order": 54, "region": "CN"},
    {"code": "CN_M1_MOM", "name": "中国M1环比", "category": "money", "unit": "%", "sort_order": 54, "region": "CN"},
    {"code": "CN_M0_ABS", "name": "中国M0数量", "category": "money", "unit": "亿元", "sort_order": 55, "region": "CN"},
    {"code": "CN_M0_YOY", "name": "中国M0同比", "category": "money", "unit": "%", "sort_order": 55, "region": "CN"},
    {"code": "CN_M0_MOM", "name": "中国M0环比", "category": "money", "unit": "%", "sort_order": 55, "region": "CN"},
    {"code": "CN_M1M2", "name": "中国M1-M2剪刀差", "category": "money", "unit": "pp", "sort_order": 56, "region": "CN"},
    {"code": "CN_REALESTATE", "name": "中国国房景气指数", "category": "macro", "unit": "点", "sort_order": 57, "region": "CN"},
    {"code": "CN_ENERGY", "name": "中国能源指数", "category": "macro", "unit": "点", "sort_order": 58, "region": "CN"},
    {"code": "CN_2Y", "name": "中国2年期国债收益率", "category": "bond", "unit": "%", "sort_order": 59, "region": "CN"},
    {"code": "CN_5Y", "name": "中国5年期国债收益率", "category": "bond", "unit": "%", "sort_order": 60, "region": "CN"},
    {"code": "CN_10Y", "name": "中国10年期国债收益率", "category": "bond", "unit": "%", "sort_order": 61, "region": "CN"},
    {"code": "CN_30Y", "name": "中国30年期国债收益率", "category": "bond", "unit": "%", "sort_order": 62, "region": "CN"},
    {"code": "CN_10Y2Y", "name": "中国10年-2年国债利差", "category": "bond", "unit": "pp", "sort_order": 63, "region": "CN"},
    # 周期模型底层数据：先进入版本化数据层，不直接铺成国家页卡片。
    # 后续综合分析栏只消费这些原始序列及其组合信号。
    {"code": "CN_PMI_PRODUCTION", "name": "制造业PMI生产指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 200, "region": "CN"},
    {"code": "CN_PMI_NEW_ORDERS", "name": "制造业PMI新订单指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 201, "region": "CN"},
    {"code": "CN_PMI_NEW_EXPORT_ORDERS", "name": "制造业PMI新出口订单指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 202, "region": "CN"},
    {"code": "CN_PMI_EMPLOYMENT", "name": "制造业PMI从业人员指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 203, "region": "CN"},
    {"code": "CN_PMI_RAW_MATERIAL_INVENTORY", "name": "制造业PMI原材料库存指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 204, "region": "CN"},
    {"code": "CN_PMI_FINISHED_GOODS_INVENTORY", "name": "制造业PMI产成品库存指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 205, "region": "CN"},
    {"code": "CN_PMI_EXPECTATIONS", "name": "制造业PMI生产经营活动预期", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 206, "region": "CN"},
    {"code": "CN_NMI_NEW_ORDERS", "name": "非制造业PMI新订单指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 207, "region": "CN"},
    {"code": "CN_NMI_EMPLOYMENT", "name": "非制造业PMI从业人员指数", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 208, "region": "CN"},
    {"code": "CN_NMI_EXPECTATIONS", "name": "非制造业PMI业务活动预期", "category": "cycle_input", "unit": "点", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 209, "region": "CN"},
    {"code": "CN_IND_REVENUE_YTD", "name": "规上工业企业营业收入累计", "category": "cycle_input", "unit": "亿元", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 210, "region": "CN"},
    {"code": "CN_IND_REVENUE_YTD_YOY", "name": "规上工业企业营业收入累计同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 211, "region": "CN"},
    {"code": "CN_IND_PROFIT_YTD", "name": "规上工业企业利润累计", "category": "cycle_input", "unit": "亿元", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 212, "region": "CN"},
    {"code": "CN_IND_PROFIT_YTD_YOY", "name": "规上工业企业利润累计同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 213, "region": "CN"},
    {"code": "CN_IND_PROFIT_MONTHLY_YOY", "name": "规上工业企业利润当月同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 214, "region": "CN"},
    {"code": "CN_IND_FINISHED_INVENTORY_YOY", "name": "规上工业企业产成品存货同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 215, "region": "CN"},
    {"code": "CN_IND_INVENTORY_DAYS", "name": "规上工业企业产成品存货周转天数", "category": "cycle_input", "unit": "天", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 216, "region": "CN"},
    {"code": "CN_TSF_STOCK_YOY", "name": "社会融资规模存量同比", "category": "cycle_input", "unit": "%", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 220, "region": "CN"},
    {"code": "CN_TSF_RMB_LOAN_STOCK_YOY", "name": "社融口径人民币贷款余额同比", "category": "cycle_input", "unit": "%", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 221, "region": "CN"},
    {"code": "CN_TSF_RMB_LOANS_FLOW", "name": "社融口径人民币贷款增量", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 222, "region": "CN"},
    {"code": "CN_CORP_BOND_FINANCING", "name": "企业债券净融资", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 223, "region": "CN"},
    {"code": "CN_GOV_BOND_FINANCING_YTD", "name": "政府债券净融资累计", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 224, "region": "CN"},
    {"code": "CN_GOV_BOND_FINANCING", "name": "政府债券净融资当月", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 225, "region": "CN"},
    {"code": "CN_GDP_NOMINAL_YTD", "name": "中国名义GDP累计", "category": "cycle_input", "unit": "亿元", "source": "NBS", "frequency": "quarterly", "is_visible": False, "sort_order": 226, "region": "CN"},
    {"code": "CN_CREDIT_INTENSITY", "name": "中国信用强度", "category": "cycle_input", "unit": "% GDP", "source": "derived", "frequency": "monthly", "is_visible": False, "sort_order": 227, "region": "CN"},
    {"code": "CN_CREDIT_IMPULSE", "name": "中国标准信用脉冲", "category": "cycle_input", "unit": "pp", "source": "derived", "frequency": "monthly", "is_visible": False, "sort_order": 228, "region": "CN"},
    {"code": "CN_TSF_YTD", "name": "社会融资规模增量累计", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 260, "region": "CN"},
    {"code": "CN_TSF_RMB_LOANS_FLOW_YTD", "name": "社融口径人民币贷款增量累计", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 261, "region": "CN"},
    {"code": "CN_CORP_BOND_FINANCING_YTD", "name": "企业债券净融资累计", "category": "cycle_input", "unit": "亿元", "source": "PBOC", "frequency": "monthly", "is_visible": False, "sort_order": 262, "region": "CN"},
    {"code": "CN_RE_INVEST_YTD", "name": "房地产开发投资累计", "category": "cycle_input", "unit": "亿元", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 230, "region": "CN"},
    {"code": "CN_RE_INVEST_YTD_YOY", "name": "房地产开发投资累计同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 231, "region": "CN"},
    {"code": "CN_RE_SALES_AREA_YTD", "name": "新建商品房销售面积累计", "category": "cycle_input", "unit": "万平方米", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 232, "region": "CN"},
    {"code": "CN_RE_SALES_AREA_YTD_YOY", "name": "新建商品房销售面积累计同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 233, "region": "CN"},
    {"code": "CN_RE_SALES_VALUE_YTD", "name": "新建商品房销售额累计", "category": "cycle_input", "unit": "亿元", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 234, "region": "CN"},
    {"code": "CN_RE_SALES_VALUE_YTD_YOY", "name": "新建商品房销售额累计同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 235, "region": "CN"},
    {"code": "CN_RE_STARTS_YTD", "name": "房屋新开工面积累计", "category": "cycle_input", "unit": "万平方米", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 236, "region": "CN"},
    {"code": "CN_RE_STARTS_YTD_YOY", "name": "房屋新开工面积累计同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 237, "region": "CN"},
    {"code": "CN_RE_CONSTRUCTION", "name": "房屋施工面积", "category": "cycle_input", "unit": "万平方米", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 238, "region": "CN"},
    {"code": "CN_RE_CONSTRUCTION_YOY", "name": "房屋施工面积同比", "category": "cycle_input", "unit": "%", "source": "NBS", "frequency": "monthly", "is_visible": False, "sort_order": 239, "region": "CN"},
    {"code": "CN_RE_PRICE_RISING_SHARE", "name": "70城新房环比上涨城市占比", "category": "cycle_input", "unit": "%", "source": "eastmoney", "frequency": "monthly", "is_visible": False, "sort_order": 240, "region": "CN"},
    {"code": "CN_RE_PRICE_MOM_MEDIAN", "name": "70城新房价格环比中位数", "category": "cycle_input", "unit": "%", "source": "eastmoney", "frequency": "monthly", "is_visible": False, "sort_order": 241, "region": "CN"},
    {"code": "CN_FISCAL_GENERAL_SPEND_YTD", "name": "全国一般公共预算支出累计", "category": "cycle_input", "unit": "亿元", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 250, "region": "CN"},
    {"code": "CN_FISCAL_GENERAL_SPEND_YOY", "name": "全国一般公共预算支出累计同比", "category": "cycle_input", "unit": "%", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 251, "region": "CN"},
    {"code": "CN_FISCAL_FUND_EXPENDITURE_YTD", "name": "全国政府性基金预算支出累计", "category": "cycle_input", "unit": "亿元", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 252, "region": "CN"},
    {"code": "CN_FISCAL_FUND_EXPENDITURE_YOY", "name": "全国政府性基金预算支出累计同比", "category": "cycle_input", "unit": "%", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 253, "region": "CN"},
    {"code": "CN_FISCAL_BROAD_EXPENDITURE_YTD", "name": "广义财政支出累计", "category": "cycle_input", "unit": "亿元", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 254, "region": "CN"},
    {"code": "CN_FISCAL_BROAD_EXPENDITURE_YOY", "name": "广义财政支出累计同比", "category": "cycle_input", "unit": "%", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 255, "region": "CN"},
    {"code": "CN_LOCAL_SPECIAL_BOND_ISSUANCE", "name": "新增地方专项债当月发行", "category": "cycle_input", "unit": "亿元", "source": "MOF", "frequency": "monthly", "is_visible": False, "sort_order": 256, "region": "CN"},
    {"code": "CN_FISCAL_SPEND_INTENSITY", "name": "广义财政支出强度", "category": "cycle_input", "unit": "% GDP", "source": "derived", "frequency": "quarterly", "is_visible": False, "sort_order": 257, "region": "CN"},
    {"code": "CN_FISCAL_IMPULSE_PROXY", "name": "财政脉冲代理", "category": "cycle_input", "unit": "pp", "source": "derived", "frequency": "quarterly", "is_visible": False, "sort_order": 258, "region": "CN"},
    {"code": "CN_CONSUMER_CONFIDENCE", "name": "消费者信心指数", "category": "cycle_input", "unit": "点", "source": "eastmoney", "frequency": "monthly", "is_visible": False, "sort_order": 260, "region": "CN"},
    {"code": "CN_CONSUMER_SATISFACTION", "name": "消费者满意指数", "category": "cycle_input", "unit": "点", "source": "eastmoney", "frequency": "monthly", "is_visible": False, "sort_order": 261, "region": "CN"},
    {"code": "CN_CONSUMER_EXPECTATIONS", "name": "消费者预期指数", "category": "cycle_input", "unit": "点", "source": "eastmoney", "frequency": "monthly", "is_visible": False, "sort_order": 262, "region": "CN"},
    {"code": "CN_ENTERPRISE_BOOM", "name": "企业景气指数", "category": "cycle_input", "unit": "点", "source": "eastmoney", "frequency": "quarterly", "is_visible": False, "sort_order": 263, "region": "CN"},
    # 自研辅助观察指标第一组：PortWatch 集装箱航运脉冲。这里先保存官方30日均值同比
    # 分量并保留抓取vintage，不急于合成总指数；shipment仅等于集装箱进出口合计，不能
    # 误称为全部海运货量。专属观测站负责把数量、咽喉通行和未来运价层分开展示。
    {"code": "PW_WLD_CNTR_SHIP_30D_YOY", "name": "全球集装箱装卸量30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 300, "region": "GLOBAL"},
    {"code": "PW_WLD_CNTR_CALLS_30D_YOY", "name": "全球集装箱船靠港30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 301, "region": "GLOBAL"},
    {"code": "PW_WLD_CNTR_IMPORT_30D_YOY", "name": "全球集装箱进口到港30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 302, "region": "GLOBAL"},
    {"code": "PW_WLD_CNTR_EXPORT_30D_YOY", "name": "全球集装箱出口装船30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 303, "region": "GLOBAL"},
    {"code": "PW_CHN_CNTR_SHIP_30D_YOY", "name": "中国集装箱装卸量30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 304, "region": "CN"},
    {"code": "PW_CHN_CNTR_CALLS_30D_YOY", "name": "中国集装箱船靠港30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 305, "region": "CN"},
    {"code": "PW_CHN_CNTR_IMPORT_30D_YOY", "name": "中国集装箱进口到港30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 306, "region": "CN"},
    {"code": "PW_CHN_CNTR_EXPORT_30D_YOY", "name": "中国集装箱出口装船30日均值同比", "category": "alternative_input", "unit": "%", "source": "IMF PortWatch", "frequency": "daily", "is_visible": False, "sort_order": 307, "region": "CN"},
    {"code": "US_CPI", "name": "美国CPI同比", "category": "macro", "unit": "%", "sort_order": 70, "region": "US"},
    {"code": "US_CORE_CPI", "name": "美国核心CPI同比", "category": "macro", "unit": "%", "sort_order": 71, "region": "US"},
    {"code": "US_IP", "name": "美国工业生产同比", "category": "macro", "unit": "%", "sort_order": 72, "region": "US"},
    {"code": "US_CLI", "name": "美国综合领先指标", "category": "macro", "unit": "点", "sort_order": 73, "region": "US"},
    {"code": "US_NFP", "name": "美国非农就业变动", "category": "macro", "unit": "万人", "source": "fred", "sort_order": 74, "region": "US"},
    {"code": "US_FFR", "name": "美国有效联邦基金利率", "category": "macro", "unit": "%", "source": "fred", "sort_order": 75, "region": "US"},
    {"code": "US_GDP", "name": "美国GDP环比折年率", "category": "macro", "unit": "%", "source": "fred", "sort_order": 76, "region": "US"},
    # 美国货币供给数据源是 FRED（美联储官方，见 fred_source.py），不是 akshare；美国没有官方
    # 常规发布的"M0"，用"货币基础"顶替；也没有对应中国"M1-M2剪刀差"的惯用指标，不加这个 tab
    {"code": "US_BASE_ABS", "name": "美国货币基础数量", "category": "money", "unit": "十亿美元", "sort_order": 77, "region": "US"},
    {"code": "US_BASE_YOY", "name": "美国货币基础同比", "category": "money", "unit": "%", "sort_order": 77, "region": "US"},
    {"code": "US_BASE_MOM", "name": "美国货币基础环比", "category": "money", "unit": "%", "sort_order": 77, "region": "US"},
    {"code": "US_M1_ABS", "name": "美国M1数量", "category": "money", "unit": "十亿美元", "sort_order": 78, "region": "US"},
    {"code": "US_M1_YOY", "name": "美国M1同比", "category": "money", "unit": "%", "sort_order": 78, "region": "US"},
    {"code": "US_M1_MOM", "name": "美国M1环比", "category": "money", "unit": "%", "sort_order": 78, "region": "US"},
    {"code": "US_M2_ABS", "name": "美国M2数量", "category": "money", "unit": "十亿美元", "sort_order": 79, "region": "US"},
    {"code": "US_M2_YOY", "name": "美国M2同比", "category": "money", "unit": "%", "sort_order": 79, "region": "US"},
    {"code": "US_M2_MOM", "name": "美国M2环比", "category": "money", "unit": "%", "sort_order": 79, "region": "US"},
    {"code": "US_2Y", "name": "美国2年期国债收益率", "category": "bond", "unit": "%", "sort_order": 80, "region": "US"},
    {"code": "US_5Y", "name": "美国5年期国债收益率", "category": "bond", "unit": "%", "sort_order": 81, "region": "US"},
    {"code": "US_10Y", "name": "美国10年期国债收益率", "category": "bond", "unit": "%", "sort_order": 82, "region": "US"},
    {"code": "US_30Y", "name": "美国30年期国债收益率", "category": "bond", "unit": "%", "sort_order": 83, "region": "US"},
    {"code": "US_10Y2Y", "name": "美国10年-2年国债利差", "category": "bond", "unit": "pp", "sort_order": 84, "region": "US"},
    {"code": "JP_CPI", "name": "日本CPI同比", "category": "macro", "unit": "%", "sort_order": 90, "region": "JP"},
    {"code": "JP_CORE_CPI", "name": "日本核心CPI同比", "category": "macro", "unit": "%", "sort_order": 91, "region": "JP"},
    {"code": "JP_IP", "name": "日本工业生产同比", "category": "macro", "unit": "%", "sort_order": 92, "region": "JP"},
    {"code": "JP_CLI", "name": "日本综合领先指标", "category": "macro", "unit": "点", "sort_order": 93, "region": "JP"},
    {"code": "JP_BOJ", "name": "日本隔夜拆借利率（月均）", "category": "macro", "unit": "%", "source": "fred", "sort_order": 94, "region": "JP"},
    # 日本没有可用的M1/M2数据源（FRED上有但已停止更新，形同死数据），用日本央行总资产
    # 代替观察日本的货币扩张力度——QQE/YCC框架下这个指标比M1/M2更贴合日本的政策传导机制，
    # 数据源同样是 FRED（见 fred_source.py），不是 akshare
    {"code": "JP_BOJ_ASSETS", "name": "日本央行总资产", "category": "macro", "unit": "亿日元", "sort_order": 95, "region": "JP"},
    {"code": "EU_CPI", "name": "欧元区HICP同比", "category": "macro", "unit": "%", "sort_order": 100, "region": "EU"},
    {"code": "EU_CORE_CPI", "name": "欧元区核心HICP同比", "category": "macro", "unit": "%", "sort_order": 101, "region": "EU"},
    {"code": "EU_IP", "name": "欧元区工业生产同比", "category": "macro", "unit": "%", "sort_order": 102, "region": "EU"},
    {"code": "EU_CLI", "name": "欧元区经济景气指数（ESI）", "category": "macro", "unit": "点", "sort_order": 103, "region": "EU"},
    {"code": "EU_ECB", "name": "欧洲央行主要再融资利率", "category": "macro", "unit": "%", "source": "fred", "sort_order": 104, "region": "EU"},
    # 欧元区没有可用的M1/M2数据源（FRED上有但已停止更新，形同死数据），用欧央行总资产
    # （周度金融报表口径）代替观察欧元区的货币扩张力度，数据源是 FRED（见 fred_source.py）
    {"code": "EU_ECB_ASSETS", "name": "欧洲央行总资产", "category": "macro", "unit": "百万欧元", "sort_order": 105, "region": "EU"},
    {"code": "EU_GDP", "name": "欧元区GDP同比", "category": "macro", "unit": "%", "sort_order": 106, "region": "EU"},
    {"code": "GB_CPI", "name": "英国CPI同比", "category": "macro", "unit": "%", "sort_order": 107, "region": "GB"},
    {"code": "GB_CORE_CPI", "name": "英国核心CPI同比", "category": "macro", "unit": "%", "sort_order": 108, "region": "GB"},
    {"code": "GB_IP", "name": "英国工业生产同比", "category": "macro", "unit": "%", "sort_order": 109, "region": "GB"},
    {"code": "GB_CLI", "name": "英国综合领先指标", "category": "macro", "unit": "点", "sort_order": 110, "region": "GB"},
    {"code": "GB_GDP", "name": "英国GDP同比", "category": "macro", "unit": "%", "sort_order": 111, "region": "GB"},
    {"code": "GB_BOE", "name": "英格兰银行利率", "category": "macro", "unit": "%", "sort_order": 112, "region": "GB"},
    {"code": "GB_M3", "name": "英国广义货币M3同比", "category": "macro", "unit": "%", "sort_order": 113, "region": "GB"},
    # 韩国没有可用的M1/M2数据源，连央行资产负债表类的替代指标都没查到能持续更新的
    # （详见 fred_source.py 注释），只能退而求其次用外汇储备顶位——但这不是货币供给概念，
    # 前端经济学解读词条会明确说明这一点，不会包装成"韩国的M0/M1/M2"
    {"code": "KR_CORE_CPI", "name": "韩国核心CPI同比", "category": "macro", "unit": "%", "sort_order": 120, "region": "KR"},
    {"code": "KR_IP", "name": "韩国工业生产同比", "category": "macro", "unit": "%", "sort_order": 121, "region": "KR"},
    {"code": "KR_CLI", "name": "韩国综合领先指标", "category": "macro", "unit": "点", "sort_order": 122, "region": "KR"},
    {"code": "KR_RESERVES", "name": "韩国外汇储备", "category": "macro", "unit": "百万美元", "sort_order": 123, "region": "KR"},
    {"code": "KR_BOK", "name": "韩国银行基准利率", "category": "macro", "unit": "%", "sort_order": 124, "region": "KR"},
]

# Keep database-sized source/frequency fields in sync with the richer modelling
# dictionary.  The catalog is exhaustive, so a newly added indicator must be
# classified before the application can silently seed it with vague defaults.
INDICATOR_DEFS = enrich_indicator_defs(INDICATOR_DEFS)
