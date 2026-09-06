import Link from "next/link";
import { notFound } from "next/navigation";
import { getIndicatorForecast, getIndicatorHistory } from "@/lib/api";
import { IndicatorDetail } from "@/components/indicator-detail";
import { EconTheory } from "@/components/econ-theory";
import { getGlossaryEntry } from "@/lib/indicator-glossary";
import type { ForecastPoint } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function IndicatorDetailPage(
  props: PageProps<"/indicators/[code]">
) {
  const { code } = await props.params;

  let history;
  try {
    history = await getIndicatorHistory(code);
  } catch {
    notFound();
  }

  let forecast: ForecastPoint[] = [];
  try {
    const forecastOut = await getIndicatorForecast(code);
    forecast = forecastOut.forecast;
  } catch {
    // 历史数据不够长时后端会拒绝预测，图表仍然只展示历史走势
  }

  const glossary = getGlossaryEntry(code);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← 返回看板
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">
        {history.name}
        {history.unit && (
          <span className="ml-2 text-base font-normal text-muted-foreground">
            ({history.unit})
          </span>
        )}
      </h1>
      {glossary && <p className="mt-1 text-sm text-muted-foreground">{glossary.meaning}</p>}
      <p className="mt-1 text-sm text-muted-foreground">
        {history.points.length} 个历史数据点
        {forecast.length > 0 ? `，未来 ${forecast.length} 天预测（虚线，含置信区间）` : ""}
      </p>

      <div className="mt-8">
        <IndicatorDetail history={history.points} forecast={forecast} />
      </div>

      {glossary && <EconTheory theory={glossary.theory} />}
    </main>
  );
}
