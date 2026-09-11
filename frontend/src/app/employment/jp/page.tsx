import Link from "next/link";
import { InternationalEmploymentDetail } from "@/components/international-employment-detail";
import { getInternationalEmployment } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function JapanEmploymentPage() {
  const employment = await getInternationalEmployment("JP");
  return <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
    <Link href="/country/jp" className="text-sm text-muted-foreground hover:underline">← 返回日本看板</Link>
    <div className="mt-3 flex flex-wrap items-end justify-between gap-4"><div><h1 className="text-2xl font-semibold">日本就业与劳动力</h1><p className="mt-1 text-sm text-muted-foreground">把低失业率放回人口收缩、女性参与和有效劳动力供给中理解</p><Link href="/population/jp" className="mt-2 inline-block text-sm text-muted-foreground hover:underline">返回人口结构，查看劳动力供给底盘 →</Link></div><div className="text-right text-xs text-muted-foreground"><div>就业数据：{employment.latest_month ?? "暂不可用"}</div><div>来源：日本劳动力调查，经OECD与FRED分发</div></div></div>
    <div className="mt-8"><InternationalEmploymentDetail data={employment} /></div>
  </main>;
}
