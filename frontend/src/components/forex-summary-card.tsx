import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { IndicatorSummary } from "@/lib/api";
import { Sparkline } from "@/components/sparkline";

export function ForexSummaryCard({ currencies }: { currencies: IndicatorSummary[] }) {
  const headline = currencies[0];
  if (!headline) return null;

  const change = headline.change_pct;
  const isUp = (change ?? 0) >= 0;

  return (
    <Link href="/forex">
      <Card className="transition-colors hover:border-foreground/30">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">汇率</CardTitle>
            <Badge variant="secondary" className="mt-1">
              {currencies.length} 个货币
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="text-xs text-muted-foreground">{headline.name}</div>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-semibold tabular-nums">
                  {headline.latest_value?.toLocaleString(undefined, {
                    maximumFractionDigits: 2,
                  }) ?? "--"}
                </span>
                {headline.unit && (
                  <span className="text-xs text-muted-foreground">{headline.unit}</span>
                )}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">点击查看全部并切换货币 →</div>
            </div>
            <Sparkline data={headline.recent_values} isUp={isUp} />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
