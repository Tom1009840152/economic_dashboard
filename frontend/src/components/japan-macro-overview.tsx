import {
  InternationalMacroOverview,
  type InternationalMacroOverviewConfig,
} from "@/components/international-macro-overview";
import type { JPMacroOverviewDashboard } from "@/lib/api";

const JAPAN_CONFIG: InternationalMacroOverviewConfig = {
  regionSlug: "jp",
  countryLabel: "日本",
  eyebrow: "日本宏观简报 · Japan",
  introduction:
    "增长与景气、就业、通胀、日本银行政策与部分金融条件分别判断；不合成黑箱总分，也不输出未经回测的衰退概率。",
  scopeNote:
    "本页只观察日本全国口径；就业数据对应日本劳动力调查，国际数据库仅作为分发路径，不与其他亚洲经济体或区域聚合值混用。",
  coverageNote:
    "当前缺少可用的日本GDP、完整日债收益率曲线、信用利差与银行贷款条件；日本银行总资产是资产负债表规模，不等同于货币供应量。",
  freshnessNote:
    "日本统计、OECD、FRED与市场数据发布节奏不同；隔夜拆借利率是月均序列，不应当作日本银行最新会议决定的实时读数。",
  policyLinkLabel: "查看日本银行利率与资产负债表",
  marketLinkLabel: "查看日股与日元市场代理",
  transmissionTitle: "日本银行政策到实体经济：当前传导链",
  transmissionDescription:
    "用月均隔夜利率、日本银行资产负债表、市场代理与实体指标组织证据；相关性不等于确定因果。",
};

export function JapanMacroOverview({
  data,
  failed,
}: {
  data: JPMacroOverviewDashboard | null;
  failed: boolean;
}) {
  return <InternationalMacroOverview data={data} failed={failed} config={JAPAN_CONFIG} />;
}
