import Link from "next/link";
import { getCurrencies, getForexForecast, getForexHistory } from "@/lib/api";
import { ForexExplorer } from "@/components/forex-explorer";
import type { DataPoint, ForecastPoint } from "@/lib/api";

export const dynamic = "force-dynamic";

const DEFAULT_BASE = "CNY";
const DEFAULT_TARGET = "USD";

export default async function ForexPage() {
  const currencies = await getCurrencies();

  const history = await getForexHistory(DEFAULT_BASE, DEFAULT_TARGET);
  let forecast: ForecastPoint[] = [];
  try {
    const forecastOut = await getForexForecast(DEFAULT_BASE, DEFAULT_TARGET);
    forecast = forecastOut.forecast;
  } catch {
    // 历史数据不够长时后端会拒绝预测，图表仍然只展示历史走势
  }

  const initialHistory: DataPoint[] = history.points;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← 返回看板
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">汇率换算</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {currencies.length} 个货币任意互换，按GDP排序
      </p>

      <div className="mt-8">
        <ForexExplorer
          currencies={currencies}
          initialBase={DEFAULT_BASE}
          initialTarget={DEFAULT_TARGET}
          initialHistory={initialHistory}
          initialForecast={forecast}
        />
      </div>
    </main>
  );
}
