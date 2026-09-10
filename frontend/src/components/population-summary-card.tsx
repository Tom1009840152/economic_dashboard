import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkline } from "@/components/sparkline";
import type { PopulationDashboard, PopulationSeries } from "@/lib/api";

function byKey(data: PopulationDashboard, key: string): PopulationSeries | undefined {
  return data.series.find((series) => series.key === key);
}

export function PopulationSummaryCard({ data }: { data: PopulationDashboard }) {
  const total = byKey(data, "total_population");
  const growth = byKey(data, "population_growth")?.points.at(-1);
  const oldShare = byKey(data, "old_share")?.points.at(-1);
  const latest = total?.points.at(-1);

  return (
    <Link href="/population/cn">
      <Card className="h-full transition-colors hover:border-foreground/30">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">人口与结构</CardTitle>
            <Badge variant="secondary" className="mt-1">长期增长底盘</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-semibold tabular-nums">
                  {latest ? (latest.value / 100_000_000).toFixed(2) : "--"}
                </span>
                <span className="text-xs text-muted-foreground">亿人</span>
              </div>
              <div className={`mt-1 text-sm tabular-nums ${(growth?.value ?? 0) >= 0 ? "text-emerald-600" : "text-red-600"}`}>
                人口增长 {growth ? `${growth.value >= 0 ? "+" : ""}${growth.value.toFixed(2)}%` : "--"}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                65岁以上 {oldShare ? `${oldShare.value.toFixed(1)}%` : "--"} · 点击分析结构 →
              </div>
            </div>
            <Sparkline
              data={(total?.points ?? []).slice(-30).map((point) => point.value)}
              isUp={(growth?.value ?? 0) >= 0}
            />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
