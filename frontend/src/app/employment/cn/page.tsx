import Link from "next/link";
import { ChinaEmploymentDetail } from "@/components/china-employment-detail";
import { getEmployment } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ChinaEmploymentPage() {
  const employment = await getEmployment("CN");

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
      <Link href="/country/cn/people" className="text-sm text-muted-foreground hover:underline">
        ← 返回人口与就业
      </Link>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">中国就业与劳动力</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            从就业总量、失业、劳动参与和就业质量判断劳动力市场，而不是只看一个失业率
          </p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>月度数据：{employment.latest_month ?? "暂不可用"}</div>
          <div>年度模型更新：{employment.wdi_updated ?? "暂不可用"}</div>
        </div>
      </div>

      <div className="mt-8">
        <ChinaEmploymentDetail data={employment} />
      </div>
    </main>
  );
}
