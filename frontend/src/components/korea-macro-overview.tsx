import {
  InternationalMacroOverview,
  type InternationalMacroOverviewConfig,
} from "@/components/international-macro-overview";
import type { KRMacroOverviewDashboard } from "@/lib/api";

const KOREA_CONFIG: InternationalMacroOverviewConfig = {
  regionSlug: "kr",
  countryLabel: "韩国",
  eyebrow: "韩国宏观简报 · Republic of Korea",
  introduction:
    "增长与景气、就业、核心通胀、韩国银行基准利率与部分金融条件分别判断；缺失数据保持缺失，不合成黑箱总分或衰退概率。",
  scopeNote:
    "本页观察韩国（大韩民国）全国口径；就业数据对应韩国经济活动人口调查，不使用朝鲜或泛亚洲聚合值。",
  coverageNote:
    "韩国银行官方基准利率事件历史已纳入；当前仍缺少可用的GDP、总体CPI、国内货币量、国债曲线与信用利差。外汇储备只是外部缓冲，不能替代政策或流动性指标。",
  freshnessNote:
    "韩国统计、OECD、FRED与市场数据发布节奏不同；基准利率按决议生效日记录，最近核验日仅表示何时确认当前值，不能当作新一次调息。",
  policyLinkLabel: "查看韩国银行基准利率与外部缓冲",
  marketLinkLabel: "查看韩股、韩元与外部代理",
  transmissionTitle: "韩国政策与金融条件到实体经济：当前证据链",
  transmissionDescription:
    "用韩国银行官方基准利率事件历史、市场代理、外部缓冲与实体指标组织证据；事后实际利率仅以核心CPI近似，因总体CPI缺失，不等同于完整的实际政策利率。",
};

export function KoreaMacroOverview({
  data,
  failed,
}: {
  data: KRMacroOverviewDashboard | null;
  failed: boolean;
}) {
  return <InternationalMacroOverview data={data} failed={failed} config={KOREA_CONFIG} />;
}
