import Link from "next/link";
import { InternationalEmploymentDetail } from "@/components/international-employment-detail";
import { getInternationalEmployment } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function EUEmploymentPage() {
  const employment = await getInternationalEmployment("EU");
  return <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
    <Link href="/country/eu" className="text-sm text-muted-foreground hover:underline">← 返回欧盟看板</Link>
    <div className="mt-3 flex flex-wrap items-end justify-between gap-4"><div><h1 className="text-2xl font-semibold">欧盟就业与劳动力</h1><p className="mt-1 text-sm text-muted-foreground">用EU27统一口径观察失业、就业、参与和更宽的劳动力闲置</p><Link href="/population/eu" className="mt-2 inline-block text-sm text-muted-foreground hover:underline">返回人口结构，查看劳动力供给底盘 →</Link></div><div className="text-right text-xs text-muted-foreground"><div>失业率数据：{employment.latest_month ?? "暂不可用"}</div><div>来源：Eurostat EU-LFS</div></div></div>
    <div className="mt-8"><InternationalEmploymentDetail data={employment} /></div>
  </main>;
}
