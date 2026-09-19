import type { Metadata } from "next";
import { MaritimeObservatory } from "@/components/maritime-observatory";
import {
  getMaritimeObservatory,
  type MaritimeObservatoryDashboard,
} from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "全球海运与贸易脉冲｜经济学看板",
  description: "以港口进出与关键航道通行构建的高频全球贸易实体流量观察。",
};

export default async function MaritimeObservatoryPage({
  searchParams,
}: {
  searchParams: Promise<{ country?: string | string[] }>;
}) {
  const params = await searchParams;
  const requestedCountry = Array.isArray(params.country) ? params.country[0] : params.country;
  const country = requestedCountry?.toUpperCase() ?? "CHN";
  let data: MaritimeObservatoryDashboard | null = null;
  let failed = false;

  try {
    data = await getMaritimeObservatory(country);
  } catch {
    failed = true;
  }

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-sky-800 dark:text-sky-300">
            另类高频指标
          </p>
          <h1 className="text-2xl font-semibold sm:text-3xl">
            {data?.title ?? "全球海运与贸易脉冲"}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            用船舶与港口的真实移动补充月度宏观指标，先观察“货有没有在动”，再分别检查拥堵与价格。
          </p>
        </div>
        <div className="max-w-sm text-xs leading-5 text-muted-foreground sm:text-right">
          第一阶段先上线可公开复核的数据；历史版本、伪实时回测和预测增益将在样本积累后补齐。
        </div>
      </div>

      <MaritimeObservatory data={data} failed={failed} />
    </main>
  );
}
