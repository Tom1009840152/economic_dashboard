import Link from "next/link";
import { ChinaPopulationDetail } from "@/components/china-population-detail";
import { getPopulation } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ChinaPopulationPage() {
  const population = await getPopulation("CN");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <Link href="/country/cn/people" className="text-sm text-muted-foreground hover:underline">
        ← 返回人口与就业
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">中国人口与经济结构</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            从人口规模、年龄队列与有效劳动力理解长期增长、需求和财政约束
          </p>
          <Link href="/employment/cn" className="mt-2 inline-block text-sm text-muted-foreground hover:underline">
            继续看人口如何转化为就业与产出 →
          </Link>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>{population.source}</div>
          <div>数据集更新：{population.last_updated ?? "未知"}</div>
        </div>
      </div>

      <div className="mt-8">
        <ChinaPopulationDetail data={population} />
      </div>
    </main>
  );
}
