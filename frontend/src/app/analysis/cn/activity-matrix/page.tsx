import Link from "next/link";
import { ChinaActivityMatrixDetail } from "@/components/china-activity-matrix-detail";
import { getChinaActivityMatrix } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ChinaActivityMatrixPage() {
  // The detail view renders a 60-month line chart and a 24-month heat map.
  // Keep the server payload aligned with that visible horizon; callers that
  // need longer research windows can still request up to 240 months via API.
  const matrix = await getChinaActivityMatrix(60);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <Link href="/country/cn/analysis" className="text-sm text-muted-foreground hover:underline">
        ← 返回综合分析
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-muted-foreground">综合分析</p>
          <h1 className="text-2xl font-semibold">中国经济周期月度活动矩阵</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            把不同频率、不同量纲的经济指标对齐到月度，并按同步活动与领先信号拆解当前动能
          </p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>最新可计算月份：{matrix.as_of ?? "--"}</div>
          <div>当前快照 · 方法 v{matrix.methodology_version}</div>
        </div>
      </div>

      <div className="mt-8">
        <ChinaActivityMatrixDetail data={matrix} />
      </div>
    </main>
  );
}
