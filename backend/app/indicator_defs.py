"""指标元信息定义，category 取值：index / forex / commodity / macro / bond。
region 取值：CN / US / JP / EU / GLOBAL，用于左侧国家 tab 分组；跨国的汇率/大宗商品
（除了直接代表某国/地区货币的那几个）归为 GLOBAL，只在"综合"页和汇率换算器里出现。

bond 类是国债收益率，同一国家会有多个期限（2Y/5Y/10Y/30Y）+ 10年-2年利差，
前端会把同一国家的 bond 指标合并成一张汇总卡，点进去用多线图一起看，
不像其它指标那样每个单独一张卡——10年-2年利差是经典的衰退先行信号，值得多个期限放在一起看。

sort_order 决定看板展示顺序：index/commodity 按品类分组在前后，
forex 组内按各经济体名义 GDP 排名排序（数据源为中国银行外汇牌价，
覆盖不到印度、巴西、俄罗斯等 GDP 靠前但该接口不提供报价的国家；
港币/澳门元因为是地区而非主权国家不选用），
macro/bond 组是月度/日度的宏观经济数据，中国的在前、美国、日本、欧盟依次在后。
"""

INDICATOR_DEFS = [
    {"code": "SSE", "name": "上证指数", "category": "index", "unit": "点", "sort_order": 0, "region": "CN"},
    {"code": "DJI", "name": "道琼斯工业指数", "category": "index", "unit": "点", "sort_order": 1, "region": "US"},
    {"code": "NKY", "name": "日经225指数", "category": "index", "unit": "点", "sort_order": 2, "region": "JP"},
    {"code": "STOXX50", "name": "欧洲斯托克50指数", "category": "index", "unit": "点", "sort_order": 3, "region": "EU"},
    {"code": "USDCNY", "name": "美元/人民币", "category": "forex", "unit": "CNY", "sort_order": 10, "region": "US"},
    {"code": "EURCNY", "name": "欧元/人民币", "category": "forex", "unit": "CNY", "sort_order": 11, "region": "EU"},
    {"code": "JPYCNY", "name": "日元/人民币", "category": "forex", "unit": "CNY/100日元", "sort_order": 12, "region": "JP"},
    {"code": "GBPCNY", "name": "英镑/人民币", "category": "forex", "unit": "CNY", "sort_order": 13, "region": "GLOBAL"},
    {"code": "CADCNY", "name": "加元/人民币", "category": "forex", "unit": "CNY", "sort_order": 14, "region": "GLOBAL"},
    {"code": "AUDCNY", "name": "澳元/人民币", "category": "forex", "unit": "CNY", "sort_order": 15, "region": "GLOBAL"},
    {"code": "KRWCNY", "name": "韩元/人民币", "category": "forex", "unit": "CNY/100韩元", "sort_order": 16, "region": "GLOBAL"},
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
    {"code": "CN_2Y", "name": "中国2年期国债收益率", "category": "bond", "unit": "%", "sort_order": 44, "region": "CN"},
    {"code": "CN_5Y", "name": "中国5年期国债收益率", "category": "bond", "unit": "%", "sort_order": 45, "region": "CN"},
    {"code": "CN_10Y", "name": "中国10年期国债收益率", "category": "bond", "unit": "%", "sort_order": 46, "region": "CN"},
    {"code": "CN_30Y", "name": "中国30年期国债收益率", "category": "bond", "unit": "%", "sort_order": 47, "region": "CN"},
    {"code": "CN_10Y2Y", "name": "中国10年-2年国债利差", "category": "bond", "unit": "pp", "sort_order": 48, "region": "CN"},
    {"code": "US_CPI", "name": "美国CPI同比", "category": "macro", "unit": "%", "sort_order": 49, "region": "US"},
    {"code": "US_NFP", "name": "美国非农就业变动", "category": "macro", "unit": "万人", "sort_order": 50, "region": "US"},
    {"code": "US_FFR", "name": "美联储联邦基金利率", "category": "macro", "unit": "%", "sort_order": 51, "region": "US"},
    {"code": "US_2Y", "name": "美国2年期国债收益率", "category": "bond", "unit": "%", "sort_order": 52, "region": "US"},
    {"code": "US_5Y", "name": "美国5年期国债收益率", "category": "bond", "unit": "%", "sort_order": 53, "region": "US"},
    {"code": "US_10Y", "name": "美国10年期国债收益率", "category": "bond", "unit": "%", "sort_order": 54, "region": "US"},
    {"code": "US_30Y", "name": "美国30年期国债收益率", "category": "bond", "unit": "%", "sort_order": 55, "region": "US"},
    {"code": "US_10Y2Y", "name": "美国10年-2年国债利差", "category": "bond", "unit": "pp", "sort_order": 56, "region": "US"},
    {"code": "JP_CPI", "name": "日本CPI同比", "category": "macro", "unit": "%", "sort_order": 57, "region": "JP"},
    {"code": "JP_BOJ", "name": "日本央行政策利率", "category": "macro", "unit": "%", "sort_order": 58, "region": "JP"},
    {"code": "EU_CPI", "name": "欧元区CPI同比", "category": "macro", "unit": "%", "sort_order": 59, "region": "EU"},
    {"code": "EU_ECB", "name": "欧洲央行利率", "category": "macro", "unit": "%", "sort_order": 60, "region": "EU"},
]
