import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkline } from "@/components/sparkline";
import type { EmploymentSeries, InternationalEmploymentDashboard } from "@/lib/api";

function byKey(data: InternationalEmploymentDashboard, key: string): EmploymentSeries | undefined {
  return data.series.find((series) => series.key === key);
}

function yearAgoPeriod(period: string): string {
  return `${Number(period.slice(0, 4)) - 1}${period.slice(4)}`;
}

export function InternationalEmploymentSummaryCard({ data }: { data: InternationalEmploymentDashboard }) {
  const unemployment = byKey(data, "unemployment");
  const employment = byKey(data, "employment_ratio")?.points.at(-1);
  const latest = unemployment?.points.at(-1);
  const yearAgo = latest
    ? unemployment?.points.find((point) => point.period === yearAgoPeriod(latest.period))
    : undefined;
  const change = latest && yearAgo ? latest.value - yearAgo.value : null;
  const badge = data.region === "EU" ? "Eurostat EU27" : "OECD可比口径";

  return (
    <Link href={`/employment/${data.region.toLowerCase()}`}>
      <Card className="h-full transition-colors hover:border-foreground/30">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">就业与劳动力</CardTitle>
            <Badge variant="secondary" className="mt-1">{badge}</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-semibold tabular-nums">{latest ? latest.value.toFixed(1) : "--"}</span>
                <span className="text-xs text-muted-foreground">%</span>
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                失业率{change !== null ? ` · 同比 ${change >= 0 ? "+" : ""}${change.toFixed(1)}pp` : ""}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                就业率 {employment ? `${employment.value.toFixed(1)}%` : "--"} · 点击分析结构 →
              </div>
            </div>
            <Sparkline
              data={(unemployment?.points ?? []).slice(-30).map((point) => point.value)}
              isUp={(change ?? 0) <= 0}
            />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
