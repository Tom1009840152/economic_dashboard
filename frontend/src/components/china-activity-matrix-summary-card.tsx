import Link from "next/link";
import { ArrowRight, ChartNoAxesCombined, Radar } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  ActivityMatrixDashboard,
  ActivityMatrixMonth,
} from "@/lib/api";

function score(value: number | null | undefined): string {
  return value == null ? "--" : value.toFixed(1);
}

function signed(value: number | null): string {
  if (value == null) return "暂无上月比较";
  return `较上月 ${value > 0 ? "+" : ""}${value.toFixed(1)}`;
}

function comparisonText(month: ActivityMatrixMonth, value: number | null): string {
  return month.comparable_to_previous
    ? signed(value)
    : "口径构成变化，不直接环比";
}

function latestMonth(data: ActivityMatrixDashboard): ActivityMatrixMonth | undefined {
  if (!data.latest) return undefined;
  return data.months.find((month) => month.period === data.latest?.period);
}

function previousMonth(
  data: ActivityMatrixDashboard,
  current: ActivityMatrixMonth | undefined,
): ActivityMatrixMonth | undefined {
  if (!current) return undefined;
  const index = data.months.findIndex((month) => month.period === current.period);
  return index > 0 ? data.months[index - 1] : undefined;
}

function difference(
  current: number | null | undefined,
  previous: number | null | undefined,
): number | null {
  return current == null || previous == null ? null : current - previous;
}

export function ChinaActivityMatrixSummaryCard({
  data,
}: {
  data: ActivityMatrixDashboard;
}) {
  const current = latestMonth(data);
  const previous = previousMonth(data, current);
  const latest = data.latest;
  const strongest = latest?.positive_contributions[0];
  const weakest = latest?.negative_contributions[0];

  return (
    <Link
      href="/analysis/cn/activity-matrix"
      className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      aria-label="查看中国经济周期月度活动矩阵详情"
    >
      <Card className="group overflow-hidden border-foreground/15 bg-gradient-to-br from-background via-background to-muted/55 transition-all hover:border-foreground/30 hover:shadow-md">
        <CardHeader className="gap-4 pb-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">
              <Radar className="size-4" aria-hidden="true" />
              增长信号底盘
            </div>
            <CardTitle className="text-xl">经济活动与领先信号</CardTitle>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              把同步活动、领先需求、信用财政、房地产和预期指标对齐到月度，观察经济动能来自哪里。
            </p>
          </div>
          <Badge variant="outline">描述性矩阵 · v{data.methodology_version}</Badge>
        </CardHeader>

        <CardContent>
          {latest && current ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-xl border bg-background/75 p-4">
                  <div className="text-xs text-muted-foreground">综合活动指数</div>
                  <div className="mt-2 text-2xl font-semibold tabular-nums">
                    {score(latest.composite_index)}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {comparisonText(current, difference(latest.composite_index, previous?.composite_index))}
                  </div>
                </div>
                <div className="rounded-xl border bg-background/75 p-4">
                  <div className="text-xs text-muted-foreground">同步活动指数</div>
                  <div className="mt-2 text-2xl font-semibold tabular-nums">
                    {score(latest.coincident_index)}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {comparisonText(current, difference(latest.coincident_index, previous?.coincident_index))}
                  </div>
                </div>
                <div className="rounded-xl border bg-background/75 p-4">
                  <div className="text-xs text-muted-foreground">领先信号指数</div>
                  <div className="mt-2 text-2xl font-semibold tabular-nums">
                    {score(latest.leading_index)}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {comparisonText(current, difference(latest.leading_index, previous?.leading_index))}
                  </div>
                </div>
                <div className="rounded-xl border bg-background/75 p-4">
                  <div className="text-xs text-muted-foreground">有效板块覆盖</div>
                  <div className="mt-2 text-2xl font-semibold tabular-nums">
                    {(latest.overall_coverage * 100).toFixed(0)}%
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    输入 {(latest.input_coverage * 100).toFixed(0)}% · 伪实时 {(latest.realtime_coverage * 100).toFixed(0)}%
                  </div>
                </div>
              </div>

              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                {current.blocks.map((block) => (
                  <div key={block.key} className="rounded-lg bg-muted/55 px-3 py-2.5">
                    <div className="truncate text-xs text-muted-foreground">{block.name}</div>
                    <div className="mt-1 flex items-baseline justify-between gap-2">
                      <span className="font-medium tabular-nums">{score(block.score)}</span>
                      <span className="text-[11px] text-muted-foreground">
                        覆盖 {(block.coverage * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>

              {(strongest || weakest) && (
                <div className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
                  <div className="rounded-lg border px-3 py-2 text-muted-foreground">
                    主要推动：<span className="font-medium text-foreground">{strongest?.name ?? "--"}</span>
                  </div>
                  <div className="rounded-lg border px-3 py-2 text-muted-foreground">
                    主要拖累：<span className="font-medium text-foreground">{weakest?.name ?? "--"}</span>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">
              当前数据覆盖尚不足以形成活动矩阵，详情页仍可查看具体缺口。
            </div>
          )}

          <div className="mt-4 flex flex-col gap-2 border-t pt-4 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <span className="inline-flex items-center gap-1.5">
              <ChartNoAxesCombined className="size-3.5" aria-hidden="true" />
              100为各指标自身历史中性基准，不代表周期阶段
            </span>
            <span className="inline-flex items-center gap-1 font-medium text-foreground">
              展开矩阵与贡献
              <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
