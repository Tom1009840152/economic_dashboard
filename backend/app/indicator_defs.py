"""指标元信息定义，category 取值：index / forex / commodity / macro / bond / money。
region 取值：CN / US / JP / EU / KR / GLOBAL，用于左侧国家 tab 分组；跨国的汇率/大宗商品
（除了直接代表某国/地区货币的那几个）归为 GLOBAL，只在"综合"页和汇率换算器里出现。

bond 类是国债收益率，同一国家会有多个期限（2Y/5Y/10Y/30Y）+ 10年-2年利差，
前端会把同一国家的 bond 指标合并成一张汇总卡，点进去用多线图一起看，
不像其它指标那样每个单独一张卡——10年-2年利差是经典的衰退先行信号，值得多个期限放在一起看。

韩国目前只有 KOSPI 指数和韩元汇率——akshare 没有免费的韩国 CPI/利率/GDP 接口，
不是本项目故意漏掉的，日本的GDP同样因为没有免费接口而缺失。

sort_order 决定看板展示顺序：index/commodity 按品类分组在前后，
forex 组内按各经济体名义 GDP 排名排序（数据源为中国银行外汇牌价，
覆盖不到印度、巴西、俄罗斯等 GDP 靠前但该接口不提供报价的国家；
港币/澳门元因为是地区而非主权国家不选用），
macro/bond 组是月度/日度的宏观经济数据，中国的在前、美国、日本、欧盟、韩国依次在后。
"""

INDICATOR_DEFS = [
    {"code": "SSE", "name": "上证指数", "category": "index", "unit": "点", "sort_order": 0, "region": "CN"},
    {"code": "DJI", "name": "道琼斯工业指数", "category": "index", "unit": "点", "sort_order": 1, "region": "US"},
    {"code": "NKY", "name": "日经225指数", "category": "index", "unit": "点", "sort_order": 2, "region": "JP"},
    {"code": "STOXX50", "name": "欧洲斯托克50指数", "category": "index", "unit": "点", "sort_order": 3, "region": "EU"},
    {"code": "KOSPI", "name": "韩国综合指数", "category": "index", "unit": "点", "sort_order": 4, "region": "KR"},
    {"code": "USDCNY", "name": "美元/人民币", "category": "forex", "unit": "CNY", "sort_order": 10, "region": "US"},
    {"code": "EURCNY", "name": "欧元/人民币", "category": "forex", "unit": "CNY", "sort_order": 11, "region": "EU"},
    {"code": "JPYCNY", "name": "日元/人民币", "category": "forex", "unit": "CNY/100日元", "sort_order": 12, "region": "JP"},
    {"code": "GBPCNY", "name": "英镑/人民币", "category": "forex", "unit": "CNY", "sort_order": 13, "region": "GLOBAL"},
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
    {"code": "CN_PPI", "name": "中国PPI同比", "category": "macro", "unit": "%", "sort_order": 41, "region": "CN"},
    {"code": "CN_PMI", "name": "中国制造业PMI", "category": "macro", "unit": "点", "sort_order": 42, "region": "CN"},
    {"code": "CN_TSF", "name": "中国社会融资规模增量", "category": "macro", "unit": "亿元", "sort_order": 43, "region": "CN"},
    {"code": "CN_GDP", "name": "中国GDP同比", "category": "macro", "unit": "%", "sort_order": 44, "region": "CN"},
    {"code": "CN_RETAIL", "name": "中国社会消费品零售总额同比", "category": "macro", "unit": "%", "sort_order": 45, "region": "CN"},
    {"code": "CN_FAI", "name": "中国固定资产投资同比", "category": "macro", "unit": "%", "sort_order": 46, "region": "CN"},
    {"code": "CN_EXPORTS", "name": "中国出口同比", "category": "macro", "unit": "%", "sort_order": 47, "region": "CN"},
    # 前端把这个当作 CN_EXPORTS 的"绝对水平"伴生指标显示在同一个详情页里，不在卡片网格里单独出现，sort_order 无实际意义
    {"code": "CN_EXPORTS_ABS", "name": "中国出口额", "category": "macro", "unit": "亿美元", "sort_order": 47, "region": "CN"},
    {"code": "CN_HOG", "name": "中国生猪现货价格指数", "category": "macro", "unit": "点", "sort_order": 48, "region": "CN"},
    # money 类：M0/M1/M2 各自有绝对值/同比/环比三种口径，前端合并成一张"货币供给"汇总卡，
    # 点进去顶部切换 M0/M1/M2，图表内切换绝对值/同比/环比，不在卡片网格里逐个铺开
    {"code": "CN_M2_ABS", "name": "中国M2数量", "category": "money", "unit": "亿元", "sort_order": 49, "region": "CN"},
    {"code": "CN_M2_YOY", "name": "中国M2同比", "category": "money", "unit": "%", "sort_order": 49, "region": "CN"},
    {"code": "CN_M2_MOM", "name": "中国M2环比", "category": "money", "unit": "%", "sort_order": 49, "region": "CN"},
    {"code": "CN_M1_ABS", "name": "中国M1数量", "category": "money", "unit": "亿元", "sort_order": 50, "region": "CN"},
    {"code": "CN_M1_YOY", "name": "中国M1同比", "category": "money", "unit": "%", "sort_order": 50, "region": "CN"},
    {"code": "CN_M1_MOM", "name": "中国M1环比", "category": "money", "unit": "%", "sort_order": 50, "region": "CN"},
    {"code": "CN_M0_ABS", "name": "中国M0数量", "category": "money", "unit": "亿元", "sort_order": 51, "region": "CN"},
    {"code": "CN_M0_YOY", "name": "中国M0同比", "category": "money", "unit": "%", "sort_order": 51, "region": "CN"},
    {"code": "CN_M0_MOM", "name": "中国M0环比", "category": "money", "unit": "%", "sort_order": 51, "region": "CN"},
    {"code": "CN_M1M2", "name": "中国M1-M2剪刀差", "category": "money", "unit": "pp", "sort_order": 52, "region": "CN"},
    {"code": "CN_REALESTATE", "name": "中国国房景气指数", "category": "macro", "unit": "点", "sort_order": 53, "region": "CN"},
    {"code": "CN_ENERGY", "name": "中国能源指数", "category": "macro", "unit": "点", "sort_order": 54, "region": "CN"},
    {"code": "CN_2Y", "name": "中国2年期国债收益率", "category": "bond", "unit": "%", "sort_order": 55, "region": "CN"},
    {"code": "CN_5Y", "name": "中国5年期国债收益率", "category": "bond", "unit": "%", "sort_order": 56, "region": "CN"},
    {"code": "CN_10Y", "name": "中国10年期国债收益率", "category": "bond", "unit": "%", "sort_order": 57, "region": "CN"},
    {"code": "CN_30Y", "name": "中国30年期国债收益率", "category": "bond", "unit": "%", "sort_order": 58, "region": "CN"},
    {"code": "CN_10Y2Y", "name": "中国10年-2年国债利差", "category": "bond", "unit": "pp", "sort_order": 59, "region": "CN"},
    {"code": "US_CPI", "name": "美国CPI同比", "category": "macro", "unit": "%", "sort_order": 60, "region": "US"},
    {"code": "US_NFP", "name": "美国非农就业变动", "category": "macro", "unit": "万人", "sort_order": 61, "region": "US"},
    {"code": "US_FFR", "name": "美联储联邦基金利率", "category": "macro", "unit": "%", "sort_order": 62, "region": "US"},
    {"code": "US_GDP", "name": "美国GDP环比折年率", "category": "macro", "unit": "%", "sort_order": 63, "region": "US"},
    # 美国货币供给数据源是 FRED（美联储官方，见 fred_source.py），不是 akshare；美国没有官方
    # 常规发布的"M0"，用"货币基础"顶替；也没有对应中国"M1-M2剪刀差"的惯用指标，不加这个 tab
    {"code": "US_BASE_ABS", "name": "美国货币基础数量", "category": "money", "unit": "十亿美元", "sort_order": 64, "region": "US"},
    {"code": "US_BASE_YOY", "name": "美国货币基础同比", "category": "money", "unit": "%", "sort_order": 64, "region": "US"},
    {"code": "US_BASE_MOM", "name": "美国货币基础环比", "category": "money", "unit": "%", "sort_order": 64, "region": "US"},
    {"code": "US_M1_ABS", "name": "美国M1数量", "category": "money", "unit": "十亿美元", "sort_order": 65, "region": "US"},
    {"code": "US_M1_YOY", "name": "美国M1同比", "category": "money", "unit": "%", "sort_order": 65, "region": "US"},
    {"code": "US_M1_MOM", "name": "美国M1环比", "category": "money", "unit": "%", "sort_order": 65, "region": "US"},
    {"code": "US_M2_ABS", "name": "美国M2数量", "category": "money", "unit": "十亿美元", "sort_order": 66, "region": "US"},
    {"code": "US_M2_YOY", "name": "美国M2同比", "category": "money", "unit": "%", "sort_order": 66, "region": "US"},
    {"code": "US_M2_MOM", "name": "美国M2环比", "category": "money", "unit": "%", "sort_order": 66, "region": "US"},
    {"code": "US_2Y", "name": "美国2年期国债收益率", "category": "bond", "unit": "%", "sort_order": 67, "region": "US"},
    {"code": "US_5Y", "name": "美国5年期国债收益率", "category": "bond", "unit": "%", "sort_order": 68, "region": "US"},
    {"code": "US_10Y", "name": "美国10年期国债收益率", "category": "bond", "unit": "%", "sort_order": 69, "region": "US"},
    {"code": "US_30Y", "name": "美国30年期国债收益率", "category": "bond", "unit": "%", "sort_order": 70, "region": "US"},
    {"code": "US_10Y2Y", "name": "美国10年-2年国债利差", "category": "bond", "unit": "pp", "sort_order": 71, "region": "US"},
    {"code": "JP_CPI", "name": "日本CPI同比", "category": "macro", "unit": "%", "sort_order": 72, "region": "JP"},
    {"code": "JP_BOJ", "name": "日本央行政策利率", "category": "macro", "unit": "%", "sort_order": 73, "region": "JP"},
    # 日本没有可用的M1/M2数据源（FRED上有但已停止更新，形同死数据），用日本央行总资产
    # 代替观察日本的货币扩张力度——QQE/YCC框架下这个指标比M1/M2更贴合日本的政策传导机制，
    # 数据源同样是 FRED（见 fred_source.py），不是 akshare
    {"code": "JP_BOJ_ASSETS", "name": "日本央行总资产", "category": "macro", "unit": "亿日元", "sort_order": 74, "region": "JP"},
    {"code": "EU_CPI", "name": "欧元区CPI同比", "category": "macro", "unit": "%", "sort_order": 75, "region": "EU"},
    {"code": "EU_ECB", "name": "欧洲央行利率", "category": "macro", "unit": "%", "sort_order": 76, "region": "EU"},
    # 欧元区没有可用的M1/M2数据源（FRED上有但已停止更新，形同死数据），用欧央行总资产
    # （周度金融报表口径）代替观察欧元区的货币扩张力度，数据源是 FRED（见 fred_source.py）
    {"code": "EU_ECB_ASSETS", "name": "欧洲央行总资产", "category": "macro", "unit": "百万欧元", "sort_order": 77, "region": "EU"},
    {"code": "EU_GDP", "name": "欧元区GDP同比", "category": "macro", "unit": "%", "sort_order": 78, "region": "EU"},
    # 韩国没有可用的M1/M2数据源，连央行资产负债表类的替代指标都没查到能持续更新的
    # （详见 fred_source.py 注释），只能退而求其次用外汇储备顶位——但这不是货币供给概念，
    # 前端经济学解读词条会明确说明这一点，不会包装成"韩国的M0/M1/M2"
    {"code": "KR_RESERVES", "name": "韩国外汇储备", "category": "macro", "unit": "百万美元", "sort_order": 79, "region": "KR"},
]
