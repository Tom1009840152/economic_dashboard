import Link from "next/link";
import { ChinaMonetaryTransmissionDetail } from "@/components/china-monetary-transmission-detail";
import { getChinaMonetaryTransmission } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ChinaMonetaryTransmissionPage() {
  const analysis = await getChinaMonetaryTransmission();

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <Link href="/country/cn/analysis" className="text-sm text-muted-foreground hover:underline">
        ← 返回综合分析
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-muted-foreground">综合分析</p>
          <h1 className="text-2xl font-semibold">货币政策与信用传导</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            从政策价格、银行间流动性到实体信用，定位宽货币在哪一环传导、在哪一环受阻
          </p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>市场观察：{analysis.freshness?.market_observation_date ?? analysis.as_of}</div>
          <div>政策利率核验：{analysis.freshness?.policy_rate_verified_through ?? "--"}</div>
          <div>信用观察期：{analysis.freshness?.credit_observation_period ?? analysis.signals.find((signal) => signal.key === "credit_impulse")?.period ?? "--"}</div>
        </div>
      </div>

      <div className="mt-8">
        <ChinaMonetaryTransmissionDetail data={analysis} />
      </div>
    </main>
  );
}
