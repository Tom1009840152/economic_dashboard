import Link from "next/link";
import { USEmploymentDetail } from "@/components/us-employment-detail";
import { getUSEmployment } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function USEmploymentPage() {
  const employment = await getUSEmployment();

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <Link href="/country/us" className="text-sm text-muted-foreground hover:underline">
        ← 返回美国看板
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">美国就业与劳动力</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            用家庭调查、企业调查和职位空缺同时观察就业数量、利用不足与工资压力
          </p>
          <Link href="/population/us" className="mt-2 inline-block text-sm text-muted-foreground hover:underline">
            返回人口结构，查看劳动力供给底盘 →
          </Link>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>就业形势数据：{employment.latest_month ?? "暂不可用"}</div>
          <div>来源：美国劳工统计局，经FRED分发</div>
        </div>
      </div>

      <div className="mt-8">
        <USEmploymentDetail data={employment} />
      </div>
    </main>
  );
}
