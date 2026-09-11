import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkline } from "@/components/sparkline";
import type { EmploymentSeries, USEmploymentDashboard } from "@/lib/api";

function byKey(data: USEmploymentDashboard, key: string): EmploymentSeries | undefined {
  return data.series.find((series) => series.key === key);
}

export function USEmploymentSummaryCard({ data }: { data: USEmploymentDashboard }) {
  const unemployment = byKey(data, "unemployment");
  const payrollChange = byKey(data, "payroll_change")?.points.at(-1);
  const latest = unemployment?.points.at(-1);
  const yearAgo = latest
    ? unemployment?.points.find((point) => point.period === `${Number(latest.period.slice(0, 4)) - 1}${latest.period.slice(4)}`)
    : undefined;
  const change = latest && yearAgo ? latest.value - yearAgo.value : null;

  return (
    <Link href="/employment/us">
      <Card className="h-full transition-colors hover:border-foreground/30">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">就业与劳动力</CardTitle>
            <Badge variant="secondary" className="mt-1">BLS双调查</Badge>
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
                U-3失业率{change !== null ? ` · 同比 ${change >= 0 ? "+" : ""}${change.toFixed(1)}pp` : ""}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                非农就业 {payrollChange ? `${payrollChange.value >= 0 ? "+" : ""}${payrollChange.value.toFixed(0)} 千` : "--"} · 点击看劳动力市场 →
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
