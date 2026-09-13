"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarClock,
  Database,
  Info,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type {
  ActivityMatrixBlockMeta,
  ActivityMatrixBlockPoint,
  ActivityMatrixContribution,
  ActivityMatrixDashboard,
  ActivityMatrixInputStatus,
  ActivityMatrixMonth,
  ActivityMatrixSignalPoint,
} from "@/lib/api";

const indexConfig = {
  coincident: { label: "同步活动指数", color: "var(--chart-2)" },
  leading: { label: "领先信号指数", color: "var(--chart-5)" },
} satisfies ChartConfig;

const CONFIDENCE_LABELS = {
  high: "高覆盖",
  medium: "中等覆盖",
  low: "低覆盖",
  insufficient: "覆盖不足",
};

const COMPARISON_REASON_LABELS: Record<string, string> = {
  no_previous_month: "缺少前月基准",
  previous_month_score_insufficient: "前月覆盖不足",
  current_month_score_insufficient: "本月覆盖不足",
  available_block_set_changed: "可用板块发生变化",
  available_signal_set_changed: "可用信号发生变化",
  effective_weight_composition_changed: "有效权重构成发生变化",
  regular_january_data_gap: "一月存在常规数据空档",
};

function formatScore(value: number | null | undefined, digits = 1): string {
  return value == null ? "--" : value.toFixed(digits);
}

function formatCoverage(value: number | null | undefined): string {
  return value == null ? "--" : `${(value * 100).toFixed(0)}%`;
}

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function roleLabel(role: "coincident" | "leading"): string {
  return role === "coincident" ? "同步" : "领先";
}

function comparisonReason(month: ActivityMatrixMonth): string {
  return (month.comparison_reasons ?? [])
    .map((reason) => COMPARISON_REASON_LABELS[reason] ?? reason)
    .join("、");
}

function heatClass(value: number | null | undefined): string {
  if (value == null) return "bg-muted/55 text-muted-foreground";
  if (value >= 112) return "bg-sky-700 text-white dark:bg-sky-500 dark:text-sky-950";
  if (value >= 105) return "bg-sky-200 text-sky-950 dark:bg-sky-800 dark:text-sky-100";
  if (value <= 88) return "bg-amber-600 text-white dark:bg-amber-500 dark:text-amber-950";
  if (value <= 95) return "bg-amber-200 text-amber-950 dark:bg-amber-800 dark:text-amber-100";
  return "bg-muted text-foreground";
}

function currentMonth(data: ActivityMatrixDashboard): ActivityMatrixMonth | undefined {
  if (!data.latest) return undefined;
  return data.months.find((month) => month.period === data.latest?.period);
}

function blockForMonth(
  month: ActivityMatrixMonth,
  key: string,
): ActivityMatrixBlockPoint | undefined {
  return month.blocks.find((block) => block.key === key);
}

function signalForBlock(
  block: ActivityMatrixBlockPoint | undefined,
  code: string,
): ActivityMatrixSignalPoint | undefined {
  return block?.signals.find((signal) => signal.code === code);
}

function latestStatusMap(data: ActivityMatrixDashboard): Map<string, ActivityMatrixInputStatus> {
  return new Map(
    (data.latest?.input_latest_periods ?? []).map((indicator) => [indicator.code, indicator]),
  );
}

function ContributionList({
  title,
  direction,
  items,
}: {
  title: string;
  direction: "positive" | "negative";
  items: ActivityMatrixContribution[];
}) {
  const Icon = direction === "positive" ? ArrowUpRight : ArrowDownRight;
  const max = Math.max(...items.map((item) => Math.abs(item.contribution)), 0.01);

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Icon className="size-4" aria-hidden="true" />
          <CardTitle>{title}</CardTitle>
        </div>
        <CardDescription>贡献值是指标经板块内、板块间等权后的指数点贡献。</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length ? (
          <ol className="space-y-3">
            {items.slice(0, 5).map((item) => (
              <li key={item.code}>
                <div className="flex items-baseline justify-between gap-3 text-xs">
                  <div className="min-w-0">
                    <span className="truncate font-medium">{item.name}</span>
                    <span className="ml-1.5 text-muted-foreground">{roleLabel(item.role)}</span>
                  </div>
                  <span className="shrink-0 font-mono tabular-nums">
                    {signed(item.contribution)}
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className={direction === "positive" ? "h-full rounded-full bg-sky-600" : "h-full rounded-full bg-amber-600"}
                    style={{ width: `${Math.max(5, Math.abs(item.contribution) / max * 100)}%` }}
                  />
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted-foreground">本月没有可计算的{title}。</p>
        )}
      </CardContent>
    </Card>
  );
}

function IndicatorDetails({
  block,
  current,
  statusByCode,
}: {
  block: ActivityMatrixBlockMeta;
  current: ActivityMatrixBlockPoint | undefined;
  statusByCode: Map<string, ActivityMatrixInputStatus>;
}) {
  const rows = block.signals.map((meta) => ({
    meta,
    point: signalForBlock(current, meta.code),
    status: statusByCode.get(meta.code),
  }));

  return (
    <details className="group rounded-xl border bg-card" open={false}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 marker:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{block.name}</span>
            <Badge variant="secondary">{roleLabel(block.role)}</Badge>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            本期可用 {current?.available_count ?? 0}/{current?.total_count ?? block.signals.length} · 板块指数 {formatScore(current?.score)}
          </div>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground group-open:hidden">展开指标</span>
        <span className="hidden shrink-0 text-xs text-muted-foreground group-open:inline">收起</span>
      </summary>

      <div className="border-t p-3 md:hidden">
        <div className="space-y-2">
          {rows.map(({ meta, point, status }) => (
            <div key={meta.code} className="rounded-lg bg-muted/45 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{meta.name}</div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">
                    {meta.sources.join(" / ")} · {meta.frequency} · {meta.direction === "positive" ? "正向" : "反向"}
                  </div>
                </div>
                <Badge variant={status?.is_stale ? "outline" : "secondary"}>
                  {status?.is_stale ? "滞后" : point?.standardized_score == null ? "缺失" : "已纳入"}
                </Badge>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                <div><div className="text-muted-foreground">采用期</div><div className="mt-1 tabular-nums">{point?.source_period ?? "--"}</div></div>
                <div><div className="text-muted-foreground">原值</div><div className="mt-1 tabular-nums">{point?.raw_value == null ? "--" : point.raw_value.toLocaleString()}</div></div>
                <div><div className="text-muted-foreground">标准分</div><div className="mt-1 tabular-nums">{formatScore(point?.standardized_score, 2)}</div></div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="hidden overflow-x-auto border-t md:block">
        <table className="w-full min-w-[760px] text-left text-xs">
          <thead className="bg-muted/45 text-muted-foreground">
            <tr>
              <th className="px-4 py-2.5 font-medium">模型信号</th>
              <th className="px-3 py-2.5 font-medium">来源 / 频率</th>
              <th className="px-3 py-2.5 font-medium">周期方向</th>
              <th className="px-3 py-2.5 font-medium">采用观察期</th>
              <th className="px-3 py-2.5 text-right font-medium">原值</th>
              <th className="px-3 py-2.5 text-right font-medium">标准分</th>
              <th className="px-4 py-2.5 text-right font-medium">贡献</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map(({ meta, point, status }) => (
              <tr key={meta.code}>
                <td className="px-4 py-3">
                  <div className="font-medium">{meta.name}</div>
                  <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">{meta.code}</div>
                </td>
                <td className="px-3 py-3 text-muted-foreground">{meta.sources.join(" / ")} · {meta.frequency}</td>
                <td className="px-3 py-3">{meta.direction === "positive" ? "正向" : "反向"}</td>
                <td className="px-3 py-3 tabular-nums">
                  {point?.source_period ?? "--"}
                  {status?.is_stale && <span className="ml-1 text-amber-700 dark:text-amber-400">滞后</span>}
                </td>
                <td className="px-3 py-3 text-right tabular-nums">
                  {point?.raw_value == null ? "--" : point.raw_value.toLocaleString()}
                </td>
                <td className="px-3 py-3 text-right font-mono tabular-nums">{formatScore(point?.standardized_score, 2)}</td>
                <td className="px-4 py-3 text-right font-mono tabular-nums">
                  {point?.contribution == null ? "--" : signed(point.contribution)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function ChinaActivityMatrixDetail({ data }: { data: ActivityMatrixDashboard }) {
  const latest = data.latest;
  const current = currentMonth(data);
  const chartRows = data.months.slice(-60).map((month) => ({
    period: month.period,
    coincident: month.coincident_index,
    leading: month.leading_index,
  }));
  const chartMonths = data.months.slice(-60);
  const nonComparableMonths = chartMonths.filter((month) => !month.comparable_to_previous);
  const heatMonths = data.months.slice(-24);
  const statusByCode = latestStatusMap(data);

  if (!latest || !current) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>暂时无法形成活动矩阵</CardTitle>
            <CardDescription>{data.methodology_note}</CardDescription>
          </CardHeader>
        </Card>
        {data.warnings.length > 0 && (
          <div className="rounded-xl border border-dashed p-5 text-sm leading-6 text-muted-foreground">
            {data.warnings.map((warning) => <p key={warning}>• {warning}</p>)}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <Card className="overflow-hidden border-foreground/15 bg-gradient-to-br from-background via-background to-muted/60">
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">最新可计算月份</div>
            <CardTitle className="text-2xl">{latest.period} 活动信号</CardTitle>
            <CardDescription className="mt-2 max-w-3xl text-sm leading-6">
              同步指数描述当期现实活动，领先指数汇总订单、信用、房地产先行需求与预期。两者都以100为各自历史中性基准。
            </CardDescription>
          </div>
          <Badge variant="outline">方法 v{data.methodology_version}</Badge>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">综合活动指数</div>
              <div className="mt-2 text-3xl font-semibold tabular-nums">{formatScore(latest.composite_index)}</div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">同步活动指数</div>
              <div className="mt-2 text-3xl font-semibold tabular-nums">{formatScore(latest.coincident_index)}</div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">领先信号指数</div>
              <div className="mt-2 text-3xl font-semibold tabular-nums">{formatScore(latest.leading_index)}</div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">有效板块覆盖</div>
              <div className="mt-2 text-3xl font-semibold tabular-nums">{formatCoverage(latest.overall_coverage)}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {CONFIDENCE_LABELS[latest.confidence]} · 输入 {formatCoverage(latest.input_coverage)} · 伪实时 {formatCoverage(latest.realtime_coverage)}
              </div>
            </div>
          </div>
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-muted/55 px-3 py-2 text-xs leading-5 text-muted-foreground">
            <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            这里展示标准化后的活动强弱与领先变化，不划分复苏、扩张、放缓或收缩；周期阶段判定将在 A2 单独建模。
          </div>
          <div className={`mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-xs leading-5 ${current.comparable_to_previous ? "bg-muted/55 text-muted-foreground" : "bg-amber-50 text-amber-900 dark:bg-amber-950/45 dark:text-amber-200"}`}>
            <CalendarClock className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {current.comparable_to_previous
              ? "本月与上月采用相同口径构成，可进行月度变化比较。"
              : `口径构成变化，不直接环比${comparisonReason(current) ? `：${comparisonReason(current)}` : ""}。`}
          </div>
        </CardContent>
      </Card>

      <section>
        <div className="mb-4">
          <h2 className="text-lg font-semibold">五个分析板块</h2>
          <p className="mt-1 text-sm text-muted-foreground">信号按经济含义设置板块内权重，五个板块各占20%，避免指标较多的板块自然超权重。</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {current.blocks.map((block) => (
            <Card key={block.key} size="sm">
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <Badge variant="secondary">{roleLabel(block.role)}</Badge>
                  <span className="text-[11px] text-muted-foreground">{block.available_count}/{block.total_count}</span>
                </div>
                <CardTitle className="pt-1">{block.name}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold tabular-nums">{formatScore(block.score)}</div>
                <div className="mt-1 text-xs text-muted-foreground">数据覆盖 {formatCoverage(block.coverage)}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">伪实时准备 {formatCoverage(block.realtime_coverage)}</div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>同步活动与领先信号</CardTitle>
          <CardDescription>最近60个月；100是各自的历史中性基准，不是PMI荣枯线，也不是周期阶段分界线。</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="sr-only">
            {latest.period}同步活动指数{formatScore(latest.coincident_index)}，领先信号指数{formatScore(latest.leading_index)}。
          </p>
          <ChartContainer
            config={indexConfig}
            className="h-[320px] w-full"
            initialDimension={{ width: 920, height: 320 }}
            role="img"
            aria-label="最近60个月同步活动指数和领先信号指数折线图，100为历史中性基准"
          >
            <LineChart data={chartRows} margin={{ left: 2, right: 8 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={32} tickFormatter={(value) => String(value).slice(2)} />
              <YAxis tickLine={false} axisLine={false} width={40} domain={["auto", "auto"]} />
              <Tooltip
                labelFormatter={(label) => {
                  const month = chartMonths.find((item) => item.period === String(label));
                  return month?.comparable_to_previous ? String(label) : `${String(label)}（不宜直接环比）`;
                }}
                formatter={(value, name) => [Number(value).toFixed(1), indexConfig[name as keyof typeof indexConfig]?.label]}
              />
              <ReferenceLine y={100} stroke="var(--border)" strokeDasharray="4 4" label={{ value: "中性100", position: "insideTopRight", fill: "var(--muted-foreground)", fontSize: 11 }} />
              {nonComparableMonths.map((month) => (
                <ReferenceLine
                  key={`comparison-${month.period}`}
                  x={month.period}
                  stroke="#d97706"
                  strokeDasharray="2 4"
                  strokeOpacity={0.55}
                />
              ))}
              <Line dataKey="coincident" stroke="var(--color-coincident)" strokeWidth={2.4} dot={false} isAnimationActive={false} />
              <Line dataKey="leading" stroke="var(--color-leading)" strokeWidth={2.4} dot={false} isAnimationActive={false} />
              <ChartLegend content={<ChartLegendContent />} />
            </LineChart>
          </ChartContainer>
          {nonComparableMonths.length > 0 && (
            <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:bg-amber-950/45 dark:text-amber-200">
              琥珀色竖虚线表示该月相对前月的可用信号或有效权重发生变化；折线仍展示当月水平，但跨线变化不应直接解释为经济环比。
              <span className="ml-1">涉及月份：{nonComparableMonths.map((month) => month.period).join("、")}</span>
            </div>
          )}
        </CardContent>
      </Card>

      <section>
        <div className="mb-4">
          <h2 className="text-lg font-semibold">外部对照指标</h2>
          <p className="mt-1 text-sm text-muted-foreground">这些指标不参与主指数评分。GDP更接近结果变量；CLI与国房景气和部分输入有成分重叠，只能辅助对照，不能当作独立样本外验证。</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {current.validation.map((point) => {
            const meta = data.validation_indicators.find((item) => item.code === point.code);
            return (
              <Card key={point.code} size="sm">
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle>{point.name}</CardTitle>
                    <Badge variant="outline">不计分</Badge>
                  </div>
                  <CardDescription>
                    {meta?.source ?? "--"} · {meta?.frequency === "quarterly" ? "季度观察期" : "观察期"} {point.source_period ?? "暂无"}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="text-xl font-semibold tabular-nums">
                    {point.raw_value == null ? "--" : `${point.raw_value.toLocaleString()}${meta?.unit ? ` ${meta.unit}` : ""}`}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">历史标准分 {formatScore(point.standardized_score, 2)}</div>
                  {point.code === "CN_GDP" && (
                    <div className="mt-1 text-[11px] leading-4 text-muted-foreground">GDP按原季度末月份定位；向后延用仅用于同月验证对照。</div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>板块热力矩阵</CardTitle>
          <CardDescription>最近24个月。蓝色高于自身历史中性，琥珀色低于中性；月份后的 * 与单元格描边表示相对前月口径不可比，缺失不按0计算。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto rounded-xl border" tabIndex={0} aria-label="可横向滚动的最近24个月板块热力矩阵">
            <table className="min-w-max border-separate border-spacing-1 p-1 text-center text-[10px]">
              <caption className="sr-only">最近24个月各活动板块标准化指数</caption>
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 min-w-36 bg-card px-2 py-2 text-left text-xs font-medium">板块</th>
                  {heatMonths.map((month) => (
                    <th
                      key={month.period}
                      className={`w-11 min-w-11 px-0.5 py-2 font-medium ${month.comparable_to_previous ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400"}`}
                      title={month.comparable_to_previous ? "与前月口径可比" : `与前月不可比：${comparisonReason(month)}`}
                    >
                      {month.period.slice(2).replace("-", ".")}{month.comparable_to_previous ? "" : "*"}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.blocks.map((block) => (
                  <tr key={block.key}>
                    <th className="sticky left-0 z-10 bg-card px-2 py-1.5 text-left text-xs font-medium">
                      <span className="block max-w-36 truncate">{block.name}</span>
                      <span className="text-[10px] font-normal text-muted-foreground">{roleLabel(block.role)}</span>
                    </th>
                    {heatMonths.map((month) => {
                      const value = blockForMonth(month, block.key)?.score;
                      return (
                        <td
                          key={month.period}
                          className={`h-9 rounded px-1 font-mono tabular-nums ${heatClass(value)} ${month.comparable_to_previous ? "" : "ring-1 ring-inset ring-amber-600/70"}`}
                          title={`${month.period} ${block.name}：${formatScore(value)}${month.comparable_to_previous ? "" : `；与前月不可比（${comparisonReason(month)}）`}`}
                          aria-label={`${month.period}${block.name}${value == null ? "缺失" : `指数${value.toFixed(1)}`}`}
                        >
                          {formatScore(value, 0)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <section>
        <div className="mb-4">
          <h2 className="text-lg font-semibold">本月推动与拖累</h2>
          <p className="mt-1 text-sm text-muted-foreground">贡献只解释本月指数构成，不代表因果关系，也不等同于对未来GDP的预测。</p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <ContributionList title="主要推动" direction="positive" items={latest.positive_contributions} />
          <ContributionList title="主要拖累" direction="negative" items={latest.negative_contributions} />
        </div>
      </section>

      <section>
        <div className="mb-4">
          <h2 className="text-lg font-semibold">模型信号采用情况</h2>
          <p className="mt-1 text-sm text-muted-foreground">展开板块可核对原始或组合值、采用期、方向、标准分和最终贡献；季度信号最多向后延用两个月。</p>
        </div>
        <div className="space-y-3">
          {data.blocks.map((block) => (
            <IndicatorDetails
              key={block.key}
              block={block}
              current={blockForMonth(current, block.key)}
              statusByCode={statusByCode}
            />
          ))}
        </div>
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Database className="size-4" aria-hidden="true" />
            <CardTitle>计算口径</CardTitle>
          </div>
          <CardDescription>{data.methodology_note}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">单边稳健标准化</div><p className="mt-1 leading-5 text-muted-foreground">每期只使用此前最多60个月，以中位数和MAD衡量位置与波动。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">固定板块权重</div><p className="mt-1 leading-5 text-muted-foreground">板块内信号按经济含义加权，五个板块固定各占20%。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">缺失处理</div><p className="mt-1 leading-5 text-muted-foreground">达到最低覆盖后动态归一；缺失值从不当作0。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">指数尺度</div><p className="mt-1 leading-5 text-muted-foreground">100为中性，10个指数点对应一个标准差。</p></div>
        </CardContent>
      </Card>

      <div className="space-y-3 rounded-xl border border-dashed p-5 text-xs leading-6 text-muted-foreground">
        <div className="flex items-center gap-2 font-medium text-foreground">
          <CalendarClock className="size-4" aria-hidden="true" />
          时间与方法边界
        </div>
        {data.warnings.map((warning) => <p key={warning}>• {warning}</p>)}
      </div>
    </div>
  );
}
