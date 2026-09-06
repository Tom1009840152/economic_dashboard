import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { IndicatorSummary } from "@/lib/api";
import { Sparkline } from "@/components/sparkline";

const CATEGORY_LABEL: Record<string, string> = {
  index: "股指",
  forex: "汇率",
  commodity: "大宗商品",
  macro: "宏观经济",
};

export function IndicatorCard({ indicator }: { indicator: IndicatorSummary }) {
  const change = indicator.change_pct;
  const isUp = (change ?? 0) >= 0;
  // 本身已经是百分比的指标，涨跌幅是"变动了几个百分点"，不是相对涨跌幅，单位要用 pp 区分开
  const changeUnit = indicator.unit === "%" ? "pp" : "%";

  return (
    <Link href={`/indicators/${indicator.code}`}>
      <Card className="transition-colors hover:border-foreground/30">
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">{indicator.name}</CardTitle>
            <Badge variant="secondary" className="mt-1">
              {CATEGORY_LABEL[indicator.category] ?? indicator.category}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-semibold tabular-nums">
                  {indicator.latest_value?.toLocaleString(undefined, {
                    maximumFractionDigits: 2,
                  }) ?? "--"}
                </span>
                {indicator.unit && (
                  <span className="text-xs text-muted-foreground">{indicator.unit}</span>
                )}
              </div>
              {change !== null && (
                <div
                  className={`text-sm tabular-nums ${isUp ? "text-emerald-600" : "text-red-600"}`}
                >
                  {isUp ? "+" : ""}
                  {change?.toFixed(2)}
                  {changeUnit}
                </div>
              )}
            </div>
            <Sparkline data={indicator.recent_values} isUp={isUp} />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
