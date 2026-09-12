import Link from "next/link";
import { InternationalEmploymentDetail } from "@/components/international-employment-detail";
import { getInternationalEmployment } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function UKEmploymentPage() {
  const employment = await getInternationalEmployment("GB");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <Link href="/country/uk/people" className="text-sm text-muted-foreground hover:underline">
        ← 返回英国看板
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">英国就业与劳动力</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            使用英国国家统计局口径观察失业、参与、就业和青年进入压力
          </p>
          <Link href="/population/uk" className="mt-2 inline-block text-sm text-muted-foreground hover:underline">
            返回人口结构，查看劳动力供给底盘 →
          </Link>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>就业形势数据：{employment.latest_month ?? "暂不可用"}</div>
          <div>来源：英国国家统计局 ONS</div>
        </div>
      </div>

      <div className="mt-8">
        <InternationalEmploymentDetail data={employment} />
      </div>
    </main>
  );
}
