import Link from "next/link";
import { ChinaBusinessCycleDetail } from "@/components/china-business-cycle-detail";
import { getChinaBusinessCycleRegime } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ChinaBusinessCyclePage() {
  const regime = await getChinaBusinessCycleRegime(120);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <Link href="/country/cn/analysis" className="text-sm text-muted-foreground hover:underline">
        ← 返回综合分析
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-muted-foreground">综合分析</p>
          <h1 className="text-2xl font-semibold">中国经济周期定位</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            用六个月共同成分平衡面板识别相对增长周期，并以领先信号、绝对荣枯锚和通胀环境交叉验证
          </p>
        </div>
        <div className="text-left text-xs text-muted-foreground sm:text-right">
          <div>数据截至：{regime.as_of ?? "--"}</div>
          <div>最近可判定月：{regime.last_decision_period ?? "--"}</div>
          <div>相对周期模型 v{regime.methodology_version}</div>
          <Link
            href="/analysis/cn/business-cycle/backtest"
            className="mt-2 inline-flex rounded-lg border px-2.5 py-1.5 font-medium text-foreground transition-colors hover:bg-muted"
          >
            查看伪实时回测 →
          </Link>
        </div>
      </div>

      <div className="mt-8">
        <ChinaBusinessCycleDetail data={regime} />
      </div>
    </main>
  );
}
