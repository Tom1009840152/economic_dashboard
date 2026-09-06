import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { IndicatorSummary } from "@/lib/api";
import { Sparkline } from "@/components/sparkline";

export function MoneySupplySummaryCard({
  region,
  indicators,
}: {
  region: string;
  indicators: IndicatorSummary[];
}) {
  const headline = indicators.find((i) => i.code.endsWith("_M2_YOY")) ?? indicators[0];
  if (!headline) return null;

  const change = headline.change_pct;
  const isUp = (change ?? 0) >= 0;
  // 美国没有官方M0概念，用"货币基础"顶替，卡片上的分组标签跟着换一下
  const groupsLabel = indicators.some((i) => i.code.includes("_BASE_"))
    ? "货币基础 / M1 / M2"
    : "M0 / M1 / M2";

  return (
    <Link href={`/money-supply/${region.toLowerCase()}`}>
      <Card className="transition-colors hover:border-foreground/30">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">货币供给</CardTitle>
            <Badge variant="secondary" className="mt-1">
              {groupsLabel}
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
                <span className="text-xs text-muted-foreground">{headline.unit}</span>
              </div>
            </div>
            <Sparkline data={headline.recent_values} isUp={isUp} />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
