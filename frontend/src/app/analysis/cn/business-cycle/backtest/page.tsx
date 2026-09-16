import Link from "next/link";
import { ChinaCycleBacktestDetail } from "@/components/china-cycle-backtest-detail";
import { getChinaBusinessCycleBacktest } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ChinaBusinessCycleBacktestPage() {
  const backtest = await getChinaBusinessCycleBacktest(120);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
        <Link href="/analysis/cn/business-cycle" className="hover:underline">← 返回经济周期定位</Link>
        <Link href="/country/cn/analysis" className="hover:underline">中国综合分析</Link>
      </div>

      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-muted-foreground">模型检验</p>
          <h1 className="text-2xl font-semibold">中国经济周期伪实时回测</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            把时间拨回当时，只使用决策日之前已经发布的数据重算阶段，再与今天最新值按同一观察端点重算的结果比较
          </p>
        </div>
        <div className="text-left text-xs text-muted-foreground sm:text-right">
          <div>回测截至：{backtest.as_of ?? "--"}</div>
          <div>严格历史版本口径</div>
          <div>决策时点：次月20日 18:00（北京时间）</div>
          <div>回测方法 v{backtest.backtest_definition.a3_methodology_version}</div>
        </div>
      </div>

      <div className="mt-8">
        <ChinaCycleBacktestDetail data={backtest} />
      </div>
    </main>
  );
}
