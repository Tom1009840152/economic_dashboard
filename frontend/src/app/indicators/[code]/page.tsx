import Link from "next/link";
import { notFound } from "next/navigation";
import { getIndicatorForecast, getIndicatorHistory } from "@/lib/api";
import { IndicatorDetail } from "@/components/indicator-detail";
import { EconTheory } from "@/components/econ-theory";
import { getGlossaryEntry } from "@/lib/indicator-glossary";
import { ABSOLUTE_COMPANION } from "@/lib/companion-indicators";
import type { ForecastPoint, IndicatorHistory } from "@/lib/api";
import { COUNTRY_REGIONS, countrySectionForIndicator } from "@/lib/country-sections";

export const dynamic = "force-dynamic";

async function loadIndicator(code: string): Promise<{
  history: IndicatorHistory;
  forecast: ForecastPoint[];
  forecastUnit: string;
} | null> {
  let history: IndicatorHistory;
  try {
    history = await getIndicatorHistory(code);
  } catch {
    return null;
  }

  let forecast: ForecastPoint[] = [];
  let forecastUnit = "";
  try {
    const forecastOut = await getIndicatorForecast(code);
    // 兼容前后端滚动重启：旧后端没有频率字段时不展示可能失真的日频预测。
    if (forecastOut.forecast_unit) {
      forecast = forecastOut.forecast;
      forecastUnit = forecastOut.forecast_unit;
    }
  } catch {
    // 历史数据不够长时后端会拒绝预测，图表仍然只展示历史走势
  }

  return { history, forecast, forecastUnit };
}

export default async function IndicatorDetailPage(
  props: PageProps<"/indicators/[code]">
) {
  const { code } = await props.params;

  const primary = await loadIndicator(code);
  if (!primary) notFound();
  const { history, forecast, forecastUnit } = primary;

  const companionCode = ABSOLUTE_COMPANION[code];
  const companion = companionCode ? await loadIndicator(companionCode) : null;

  const glossary = getGlossaryEntry(code);
  const countryContext = countrySectionForIndicator(code);
  const backHref = countryContext
    ? `/country/${countryContext.region}/${countryContext.section}`
    : "/";
  const countryLabel = countryContext
    ? COUNTRY_REGIONS.find((item) => item.region === countryContext.region)?.label
    : null;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <Link href={backHref} className="text-sm text-muted-foreground hover:underline">
        ← {countryLabel ? `返回${countryLabel}栏目` : "返回看板"}
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
        {forecast.length > 0
          ? `，未来 ${forecast.length} ${forecastUnit}基线预测（虚线，含置信区间）`
          : ""}
      </p>

      <div className="mt-8">
        {companion && <h2 className="mb-3 text-sm font-semibold text-muted-foreground">同比增速</h2>}
        <IndicatorDetail history={history.points} forecast={forecast} />
      </div>

      {companion && (
        <div className="mt-10">
          <h2 className="mb-1 text-sm font-semibold text-muted-foreground">
            绝对水平 · {companion.history.name}（{companion.history.unit}）
          </h2>
          <p className="mb-3 text-xs text-muted-foreground">
            同比看的是增长动能，这里看的是规模本身——两者结合才是完整的图景
          </p>
          <IndicatorDetail history={companion.history.points} forecast={companion.forecast} />
        </div>
      )}

      {glossary && <EconTheory theory={glossary.theory} />}
    </main>
  );
}
