import Link from "next/link";
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  CalendarRange,
  CheckCircle2,
  CircleDashed,
  Compass,
  Database,
  Gauge,
  GitCompareArrows,
  Info,
  Route,
  ThermometerSun,
  TriangleAlert,
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
  absoluteAnchorQualifier,
  comparableMonthExplanation,
  contributionExplanation,
  currentPhaseForDisplay,
  phaseAxisSummary,
  phasePlainHeadline,
  previousComparableMonth,
  regimeHeadline,
  stateChangeExplanation,
} from "@/lib/china-business-cycle-presentation";
import type {
  BusinessCycleDriverDecomposition,
  BusinessCyclePhase,
  BusinessCyclePhaseStatus,
  BusinessCycleRegimeDashboard,
  BusinessCycleRegimePoint,
  BusinessCycleTrajectoryPoint,
} from "@/lib/api";

const PHASE_LABELS: Record<BusinessCyclePhase, string> = {
  recovery: "复苏",
  expansion: "扩张",
  slowdown: "放缓",
  contraction: "收缩",
};

const PHASE_COLORS: Record<BusinessCyclePhase, string> = {
  recovery: "#0284c7",
  expansion: "#059669",
  slowdown: "#d97706",
  contraction: "#dc2626",
};

const STATUS_LABELS: Record<BusinessCyclePhaseStatus, string> = {
  confirmed: "本月可判 · 已确认",
  candidate: "候选 · 待连续确认",
  transition: "本月可判 · 切换观察",
  held_uncomparable: "本月不可判 · 沿用历史",
  stale: "连续不可判 · 当前判断已过期",
  insufficient: "本月不可判",
};

const CONFIDENCE_LABELS = {
  high: "高",
  medium: "中等",
  low: "偏低",
  insufficient: "不足",
};

const LEADING_DIRECTION_LABELS = {
  up: "向上",
  down: "转弱",
  neutral: "中性",
  unavailable: "不可判断",
};

const LEADING_CONFIRMATION_LABELS = {
  confirmed: "与同步方向相互确认",
  divergent: "与同步状态背离",
  neutral: "尚未给出方向确认",
  unavailable: "领先证据不足",
};

const INFLATION_STATE_LABELS = {
  deflation_pressure: "通缩压力",
  low_inflation: "低通胀",
  moderate: "温和通胀",
  heating: "通胀升温",
  unavailable: "数据不足",
};

const INFLATION_DIRECTION_LABELS = {
  reflation: "再通胀方向",
  disinflation: "通胀回落方向",
  stable: "大体稳定",
  unavailable: "方向不可判断",
};

const ABSOLUTE_STATE_LABELS = {
  expansionary: "扩张",
  mixed: "分化",
  contractionary: "收缩",
  unavailable: "数据不足",
};

function phaseName(phase: BusinessCyclePhase | null): string {
  return phase ? PHASE_LABELS[phase] : "暂未定位";
}

function number(value: number | null | undefined, digits = 1): string {
  return value == null ? "--" : value.toFixed(digits);
}

function signed(value: number | null | undefined, digits = 1): string {
  if (value == null) return "--";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function statusClass(status: BusinessCyclePhaseStatus): string {
  if (status === "confirmed") {
    return "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200";
  }
  if (status === "candidate" || status === "transition") {
    return "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200";
  }
  return "border-border bg-muted/60 text-muted-foreground";
}

function phasePillClass(phase: BusinessCyclePhase): string {
  return {
    recovery: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-200",
    expansion: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
    slowdown: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
    contraction: "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-200",
  }[phase];
}

function phaseExplanation(latest: BusinessCycleRegimePoint): string {
  if (latest.phase_status === "held_uncomparable") {
    const diagnostic = latest.raw_phase && latest.raw_phase !== latest.phase
      ? ` 当前诊断坐标落在${phaseName(latest.raw_phase)}，但不构成正式切换证据。`
      : "";
    return latest.confirmed_phase
      ? `本月口径不可比，模型沿用最近一次已确认判断；未确认候选已重置，不把诊断性动量当作阶段切换证据。${diagnostic}`
      : `本月口径不可比，模型尚未形成正式阶段；未确认候选已重置。${diagnostic}`;
  }
  if (latest.phase_status === "stale") {
    const diagnostic = latest.raw_phase && latest.raw_phase !== latest.phase
      ? ` 当前诊断坐标落在${phaseName(latest.raw_phase)}，但不构成正式切换证据。`
      : "";
    return latest.confirmed_phase
      ? `同步成分已连续多月不可比，当前阶段判断已经过期；最近一次确认仅作历史参考。${diagnostic}`
      : `同步成分已连续多月不可比，模型尚未形成正式阶段判断。${diagnostic}`;
  }
  if (latest.phase_status === "candidate" && latest.candidate_phase) {
    return `目前出现${phaseName(latest.candidate_phase)}候选，已连续${latest.candidate_streak}个月，尚未满足确认规则。`;
  }
  if (latest.phase_status === "transition" && latest.candidate_phase) {
    return `当前已确认阶段继续有效；${phaseName(latest.candidate_phase)}候选已连续${latest.candidate_streak}个月，尚未满足切换确认规则。`;
  }
  if (latest.confirmed) {
    return "当前水平、动能与连续性规则共同满足，阶段已经确认。";
  }
  return "当前可用证据不足，暂不形成新的阶段判断。";
}

type CompassPoint = BusinessCycleTrajectoryPoint & {
  xValue: number;
  yValue: number;
  diagnostic: boolean;
};

function CycleCompass({
  trajectory,
  levelBuffer,
  momentumBuffer,
}: {
  trajectory: BusinessCycleTrajectoryPoint[];
  levelBuffer: number;
  momentumBuffer: number;
}) {
  const points: CompassPoint[] = trajectory.flatMap((point) => {
    const yValue = point.momentum_3m ?? point.diagnostic_momentum_3m;
    if (point.level_gap == null || yValue == null) return [];
    return [{
      ...point,
      xValue: point.level_gap,
      yValue,
      diagnostic: !point.comparable,
    }];
  });
  const extent = Math.max(
    3,
    levelBuffer * 1.6,
    momentumBuffer * 1.6,
    ...points.flatMap((point) => [Math.abs(point.xValue), Math.abs(point.yValue)]),
  ) * 1.15;
  const plot = { left: 70, top: 44, width: 500, height: 300 };
  const toX = (value: number) => plot.left + ((value + extent) / (2 * extent)) * plot.width;
  const toY = (value: number) => plot.top + ((extent - value) / (2 * extent)) * plot.height;
  const linePoints = points.map((point) => `${toX(point.xValue)},${toY(point.yValue)}`).join(" ");
  const latest = points.at(-1);

  return (
    <div>
      <svg
        viewBox="0 0 640 400"
        className="h-auto w-full"
        role="img"
        aria-labelledby="cycle-compass-title cycle-compass-description"
      >
        <title id="cycle-compass-title">相对增长周期四象限罗盘</title>
        <desc id="cycle-compass-description">横轴为同步活动相对历史中性的水平，纵轴为三个月动能；轨迹展示最近月份，空心点为换篮子或其他不可用于阶段判定的月份。</desc>
        <rect x={plot.left} y={plot.top} width={plot.width / 2} height={plot.height / 2} fill="#0284c7" opacity="0.055" />
        <rect x={plot.left + plot.width / 2} y={plot.top} width={plot.width / 2} height={plot.height / 2} fill="#059669" opacity="0.055" />
        <rect x={plot.left} y={plot.top + plot.height / 2} width={plot.width / 2} height={plot.height / 2} fill="#dc2626" opacity="0.045" />
        <rect x={plot.left + plot.width / 2} y={plot.top + plot.height / 2} width={plot.width / 2} height={plot.height / 2} fill="#d97706" opacity="0.055" />
        <rect
          x={toX(-levelBuffer)}
          y={plot.top}
          width={toX(levelBuffer) - toX(-levelBuffer)}
          height={plot.height}
          fill="currentColor"
          opacity="0.055"
        />
        <rect
          x={plot.left}
          y={toY(momentumBuffer)}
          width={plot.width}
          height={toY(-momentumBuffer) - toY(momentumBuffer)}
          fill="currentColor"
          opacity="0.055"
        />
        <rect x={plot.left} y={plot.top} width={plot.width} height={plot.height} fill="none" stroke="currentColor" opacity="0.18" />
        <line x1={plot.left + plot.width / 2} y1={plot.top} x2={plot.left + plot.width / 2} y2={plot.top + plot.height} stroke="currentColor" opacity="0.25" strokeDasharray="4 4" />
        <line x1={plot.left} y1={plot.top + plot.height / 2} x2={plot.left + plot.width} y2={plot.top + plot.height / 2} stroke="currentColor" opacity="0.25" strokeDasharray="4 4" />
        {[-levelBuffer, levelBuffer].map((value) => (
          <line key={`level-${value}`} x1={toX(value)} y1={plot.top} x2={toX(value)} y2={plot.top + plot.height} stroke="currentColor" opacity="0.18" strokeDasharray="2 4" />
        ))}
        {[-momentumBuffer, momentumBuffer].map((value) => (
          <line key={`momentum-${value}`} x1={plot.left} y1={toY(value)} x2={plot.left + plot.width} y2={toY(value)} stroke="currentColor" opacity="0.18" strokeDasharray="2 4" />
        ))}

        <text x={plot.left + 14} y={plot.top + 23} fontSize="13" fontWeight="600" fill="#0284c7">复苏</text>
        <text x={plot.left + plot.width - 14} y={plot.top + 23} fontSize="13" fontWeight="600" fill="#059669" textAnchor="end">扩张</text>
        <text x={plot.left + 14} y={plot.top + plot.height - 13} fontSize="13" fontWeight="600" fill="#dc2626">收缩</text>
        <text x={plot.left + plot.width - 14} y={plot.top + plot.height - 13} fontSize="13" fontWeight="600" fill="#d97706" textAnchor="end">放缓</text>

        <text x={plot.left + plot.width / 2} y={plot.top + plot.height + 34} fontSize="11" fill="currentColor" opacity="0.65" textAnchor="middle">同步活动水平：低于历史中性 ← 0 → 高于历史中性</text>
        <text x="17" y={plot.top + plot.height / 2} fontSize="11" fill="currentColor" opacity="0.65" textAnchor="middle" transform={`rotate(-90 17 ${plot.top + plot.height / 2})`}>三月动能：下行 ← 0 → 上行</text>

        {points.length > 1 && (
          <polyline points={linePoints} fill="none" stroke="currentColor" strokeWidth="1.7" opacity="0.38" strokeLinejoin="round" />
        )}
        {points.map((point, index) => {
          const isLatest = index === points.length - 1;
          const phaseColor = point.phase ? PHASE_COLORS[point.phase] : "currentColor";
          return (
            <circle
              key={`${point.period}-${index}`}
              cx={toX(point.xValue)}
              cy={toY(point.yValue)}
              r={isLatest ? 7 : 4}
              fill={point.diagnostic ? "var(--background)" : phaseColor}
              stroke={phaseColor}
              strokeWidth={isLatest ? 3 : 1.5}
              strokeDasharray={point.diagnostic ? "2 2" : undefined}
              opacity={isLatest ? 1 : 0.58 + index / Math.max(points.length, 1) * 0.3}
            >
              <title>{`${point.period}：水平差${signed(point.xValue, 2)}，${point.diagnostic ? "暂停判定动能" : "判定动能"}${signed(point.yValue, 2)}`}</title>
            </circle>
          );
        })}
        {latest && (
          <text x={toX(latest.xValue) + 10} y={toY(latest.yValue) - 10} fontSize="11" fontWeight="600" fill="currentColor">{latest.period}</text>
        )}
      </svg>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-foreground/60" />可比月份</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-full border border-dashed border-amber-600 bg-background" />换篮子/诊断点，不参与阶段切换</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 bg-foreground/10" />灰色十字带：水平 ±{levelBuffer.toFixed(1)}、动能 ±{momentumBuffer.toFixed(1)} 的判定死区</span>
        <span>坐标为相对自身历史的标准化信号，不是GDP增速；落入死区不会单独触发阶段切换。</span>
      </div>
    </div>
  );
}

function AxisDriverList({
  title,
  description,
  decomposition,
  metric,
  limit = 7,
  advisory,
}: {
  title: string;
  description: string;
  decomposition: BusinessCycleDriverDecomposition;
  metric: "level" | "momentum";
  limit?: number;
  advisory?: string;
}) {
  const field = metric === "level" ? "level_contribution" : "momentum_contribution";
  const items = [...decomposition.drivers]
    .sort((left, right) => Math.abs(right[field]) - Math.abs(left[field]));
  const maximum = Math.max(...items.map((item) => Math.abs(item[field])), 0.01);
  const visibleItems = items.slice(0, limit);
  const target = metric === "level" ? decomposition.level_gap : decomposition.momentum_3m;
  const sum = metric === "level"
    ? decomposition.level_contribution_sum
    : decomposition.momentum_contribution_sum;
  const residual = metric === "level"
    ? decomposition.level_residual
    : decomposition.momentum_residual;

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-center gap-2">
          {metric === "level"
            ? <ArrowRight className="size-4" aria-hidden="true" />
            : <GitCompareArrows className="size-4" aria-hidden="true" />}
          <CardTitle>{title}</CardTitle>
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {decomposition.status === "available" && items.length > 0 ? (
          <div className="space-y-4">
            {advisory && (
              <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-950 dark:bg-amber-950/45 dark:text-amber-200">{advisory}</div>
            )}
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-muted/55 p-3">
                <div className="text-muted-foreground">模型坐标</div>
                <div className="mt-1 text-xl font-semibold tabular-nums">{signed(target, 2)}</div>
              </div>
              <div className="rounded-lg bg-muted/55 p-3">
                <div className="text-muted-foreground">逐项加总</div>
                <div className="mt-1 text-xl font-semibold tabular-nums">{signed(sum, 2)}</div>
              </div>
            </div>
          <ol className="space-y-3">
            {visibleItems.map((item) => {
              const contribution = item[field];
              const positive = contribution >= 0;
              const Icon = positive ? ArrowUpRight : ArrowDownRight;
              return (
              <li key={item.code}>
                <div className="flex items-baseline justify-between gap-3 text-xs">
                  <div className="min-w-0">
                    <span className="inline-flex items-center gap-1 font-medium">
                      <Icon className={`size-3 ${positive ? "text-sky-600" : "text-amber-600"}`} aria-hidden="true" />
                      {item.name}
                    </span>
                    <span className="ml-1.5 text-muted-foreground">权重 {(item.effective_weight * 100).toFixed(1)}%</span>
                  </div>
                  <span className="shrink-0 font-mono tabular-nums">{signed(contribution, 2)}</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className={positive ? "h-full rounded-full bg-sky-600" : "h-full rounded-full bg-amber-600"}
                    style={{ width: `${Math.max(5, Math.abs(contribution) / maximum * 100)}%` }}
                  />
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {metric === "level"
                    ? `近3月标准分 ${signed(item.recent_score, 2)}`
                    : `近3月 ${signed(item.recent_score, 2)}，此前3月 ${signed(item.comparison_score, 2)}`}
                </div>
                <div className="mt-0.5 text-[10px] leading-4 text-muted-foreground">
                  {metric === "level"
                    ? `采用期：${item.recent_source_periods.join(" / ") || "--"}`
                    : `近端采用期：${item.recent_source_periods.join(" / ") || "--"}；对照期：${item.comparison_source_periods.join(" / ") || "--"}`}
                </div>
              </li>
              );
            })}
          </ol>
            {items.length > visibleItems.length && (
              <div className="text-[11px] text-muted-foreground">这里只展示影响绝对值最大的 {visibleItems.length} 项；加总校验仍包含全部 {items.length} 项。</div>
            )}
            <div className={`rounded-lg px-3 py-2 text-xs leading-5 ${decomposition.additivity_passed ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/45 dark:text-emerald-200" : "bg-red-50 text-red-900 dark:bg-red-950/45 dark:text-red-200"}`}>
              {decomposition.additivity_passed ? "加总校验通过" : "加总校验未通过"}：残差 {signed(residual, 6)} 点。
              窗口为 {decomposition.comparison_window_start}—{decomposition.comparison_window_end} 对比 {decomposition.recent_window_start}—{decomposition.recent_window_end}。
            </div>
          </div>
        ) : (
          <p className="text-sm leading-6 text-muted-foreground">六个月共同指标篮子不足，暂不能对这一坐标做严格分解；空值不表示贡献为零。</p>
        )}
      </CardContent>
    </Card>
  );
}

export function ChinaBusinessCycleDetail({ data }: { data: BusinessCycleRegimeDashboard }) {
  const latest = data.latest;

  if (!latest) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>暂时无法定位相对增长周期</CardTitle>
            <CardDescription>当前活动矩阵尚未达到周期定位所需的覆盖和连续性要求。</CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/analysis/cn/activity-matrix" className="inline-flex items-center gap-1 text-sm font-medium hover:underline">
              查看活动矩阵的数据缺口 <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
          </CardContent>
        </Card>
        {data.warnings.length > 0 && (
          <div className="rounded-xl border border-dashed p-5 text-sm leading-6 text-muted-foreground">
            {data.warnings.map((warning) => <p key={warning}>• {warning}</p>)}
          </div>
        )}
      </div>
    );
  }

  const candidateProgress = latest.required_confirmation_months == null
    ? null
    : Math.min(latest.candidate_streak / latest.required_confirmation_months, 1);
  const currentPhase = currentPhaseForDisplay(latest);
  const breadth = latest.absolute_anchor.breadth;
  const relativeImprovementAbsoluteWeakness =
    (currentPhase === "recovery" || currentPhase === "expansion") &&
    latest.absolute_anchor.state === "contractionary";
  const previousComparable = previousComparableMonth(data.months, latest);
  const headline = regimeHeadline(latest, data.last_decision_period);

  return (
    <div className="space-y-8">
      <Card className="overflow-hidden border-foreground/20 bg-gradient-to-br from-background via-background to-muted/65">
        <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">
              <Route className="size-4" aria-hidden="true" />
              相对增长周期
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <CardTitle className="text-3xl sm:text-4xl">
                {headline.title}
              </CardTitle>
              <Badge variant="outline" className={statusClass(latest.phase_status)}>{STATUS_LABELS[latest.phase_status]}</Badge>
            </div>
            <div className="mt-2 text-base font-semibold text-muted-foreground sm:text-lg">
              {headline.secondary}
            </div>
            <div className="mt-4 max-w-3xl rounded-xl border bg-background/75 px-4 py-3">
              <div className="text-[11px] font-medium tracking-wide text-muted-foreground">一句话解读</div>
              <div className="mt-1 text-xl font-semibold">{phasePlainHeadline(latest)}</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {phaseAxisSummary(latest)}{absoluteAnchorQualifier(latest)}
              </p>
            </div>
            <CardDescription className="mt-3 max-w-3xl text-sm leading-6">{latest.summary}</CardDescription>
          </div>
          <div className="shrink-0 text-left text-xs text-muted-foreground sm:text-right">
            <div>数据截至 {latest.period}</div>
            {headline.timing.map((item) => <div key={item} className="mt-1">{item}</div>)}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">同步平衡面板（三月均值）</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">{number(latest.level_3m)}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                较历史中性 {signed(latest.level_gap)} · 共同成分覆盖 {latest.coincident_basis_coverage == null ? "--" : `${(latest.coincident_basis_coverage * 100).toFixed(0)}%`}
              </div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">平衡面板三月动能</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">{latest.momentum_3m == null ? "不可比" : signed(latest.momentum_3m)}</div>
              {latest.coincident_basis_changed && latest.momentum_3m != null && (
                <div className="mt-1 text-xs text-amber-700 dark:text-amber-300">共同篮子发生变化，本月数值展示但暂停阶段判定</div>
              )}
              {latest.momentum_3m == null && latest.diagnostic_momentum_3m != null && (
                <div className="mt-1 text-xs text-muted-foreground">原A1诊断值 {signed(latest.diagnostic_momentum_3m)}，不参与判定</div>
              )}
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">领先信号</div>
              <div className="mt-2 text-lg font-semibold">{LEADING_DIRECTION_LABELS[latest.leading_direction]}</div>
              <div className="mt-1 text-xs text-muted-foreground">{LEADING_CONFIRMATION_LABELS[latest.leading_confirmation]}</div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">本期证据质量</div>
              <div className="mt-2 text-2xl font-semibold">{CONFIDENCE_LABELS[latest.confidence]}</div>
              <div className="mt-1 text-xs leading-5 text-muted-foreground">
                {latest.decision_reasons[0] ?? latest.confidence_reasons[0] ?? (latest.decision_eligible ? "本月可参与阶段判断" : "本月不触发阶段更新")}
              </div>
              <div className="mt-1 text-[11px] leading-4 text-muted-foreground">只衡量本期覆盖、口径可比性与规则满足度</div>
            </div>
          </div>

          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs font-medium">与上一可比月相比</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {comparableMonthExplanation(latest, previousComparable)}
              </p>
              <p className="mt-2 border-t pt-2 text-xs leading-5 text-muted-foreground">
                {stateChangeExplanation(latest)}
              </p>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs font-medium">模型为何得到当前读数</div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {contributionExplanation(latest)}
              </p>
            </div>
          </div>

          <div className="mt-3 text-xs leading-5 text-muted-foreground">
            “本期证据质量”不是历史可靠性评分。数据修订会不会改写旧判断，请看
            <Link href="/analysis/cn/business-cycle/backtest" className="ml-1 font-medium text-foreground hover:underline">
              伪实时回测
            </Link>
            。
          </div>

          <div className={`mt-4 rounded-xl px-4 py-3 text-sm leading-6 ${latest.phase_status === "confirmed" ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/45 dark:text-emerald-200" : "bg-amber-50 text-amber-950 dark:bg-amber-950/45 dark:text-amber-200"}`}>
            <div className="flex items-start gap-2">
              {latest.phase_status === "confirmed" ? <CheckCircle2 className="mt-1 size-4 shrink-0" aria-hidden="true" /> : <CircleDashed className="mt-1 size-4 shrink-0" aria-hidden="true" />}
              <div>
                <div className="font-medium">
                  {latest.phase_status === "confirmed"
                    ? "阶段已确认"
                    : latest.phase_status === "transition"
                      ? "原阶段有效，切换观察中"
                      : latest.phase_basis === "carried_forward"
                        ? "本月不形成新判断"
                        : latest.phase_basis === "pending_confirmation"
                          ? "候选阶段尚未确认"
                          : "尚无已确认阶段"}
                </div>
                <p className="mt-0.5 text-xs leading-5">{phaseExplanation(latest)}</p>
              </div>
            </div>
            {latest.candidate_phase && latest.required_confirmation_months != null && (
              <div className="mt-3">
                <div className="mb-1 flex justify-between gap-3 text-[11px]">
                  <span>候选：{phaseName(latest.candidate_phase)}</span>
                  <span>{latest.candidate_streak}/{latest.required_confirmation_months}个月</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
                  <div className="h-full rounded-full bg-current opacity-55" style={{ width: `${(candidateProgress ?? 0) * 100}%` }} />
                </div>
              </div>
            )}
            {!latest.decision_eligible && latest.decision_reasons.length > 0 && (
              <div className="mt-3 rounded-lg border border-current/15 px-3 py-2 text-xs leading-5">
                {latest.phase_basis === "carried_forward" && `已连续 ${latest.carry_forward_months} 个月未更新判断。`}
                {latest.decision_reasons[0]}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <section className="grid gap-4 xl:grid-cols-[1.55fr_0.85fr]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Compass className="size-5" aria-hidden="true" />
              <CardTitle>周期罗盘</CardTitle>
            </div>
            <CardDescription>最近12期轨迹。水平与动能均在同一组共同成分和权重上重算；四象限仅定义相对增长周期。</CardDescription>
          </CardHeader>
          <CardContent>
            <CycleCompass
              trajectory={data.trajectory}
              levelBuffer={data.methodology.level_buffer}
              momentumBuffer={data.methodology.momentum_buffer}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>领先展望</CardTitle>
            <CardDescription>领先信号只提示风险方向，不给出确定的下一阶段或切换日期。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-xl bg-muted/55 p-4">
              <div className="text-xs text-muted-foreground">领先平衡面板（三月均值）</div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{number(latest.leading_level_3m)}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                较历史中性 {signed(latest.leading_gap)} · 共同成分覆盖 {latest.leading_basis_coverage == null ? "--" : `${(latest.leading_basis_coverage * 100).toFixed(0)}%`}
              </div>
            </div>
            <div className="rounded-xl bg-muted/55 p-4">
              <div className="text-xs text-muted-foreground">领先平衡面板三月动能</div>
              <div className="mt-1 text-xl font-semibold tabular-nums">{latest.leading_momentum_3m == null ? "不可比" : signed(latest.leading_momentum_3m)}</div>
              {latest.leading_basis_changed && latest.leading_momentum_3m != null && (
                <div className="mt-1 text-xs text-amber-700 dark:text-amber-300">共同篮子发生变化，本月不作为领先确认</div>
              )}
              {latest.leading_momentum_3m == null && latest.leading_diagnostic_momentum_3m != null && (
                <div className="mt-1 text-xs text-muted-foreground">原A1诊断值 {signed(latest.leading_diagnostic_momentum_3m)}，不用于确认</div>
              )}
            </div>
            <p className="text-sm leading-6 text-muted-foreground">{latest.outlook}</p>
          </CardContent>
        </Card>
      </section>

      <section>
        <div className="mb-4 flex items-start gap-2">
          <CalendarRange className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <h2 className="text-lg font-semibold">模型确认时间线</h2>
            <p className="mt-1 text-sm text-muted-foreground">起点是模型完成确认的月份，不是对真实经济拐点的回填；候选月不会提前改写历史。</p>
          </div>
        </div>
        {data.timeline.length > 0 ? (
          <ol className="relative space-y-3 border-l pl-5 sm:ml-2">
            {data.timeline.slice(-8).map((item) => (
            <li key={`${item.phase}-${item.start_period}`} className="relative">
              <span className="absolute -left-[25px] top-4 size-2.5 rounded-full border-2 border-background" style={{ backgroundColor: PHASE_COLORS[item.phase] }} />
              <div className="rounded-xl border bg-card p-4 sm:flex sm:items-center sm:justify-between sm:gap-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className={phasePillClass(item.phase)}>{item.phase_label}</Badge>
                  {item.ongoing && <Badge variant="secondary">当前</Badge>}
                </div>
                <div className="mt-2 text-xs text-muted-foreground sm:mt-0 sm:text-right">
                  <div>{item.start_period} — {item.ongoing ? "至今" : item.end_period}</div>
                  <div className="mt-0.5">{item.duration_months}个月</div>
                </div>
              </div>
            </li>
            ))}
          </ol>
        ) : (
          <div className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">当前历史窗口内还没有可展示的已确认阶段。</div>
        )}
      </section>

      <section>
        <div className="mb-4 flex items-start gap-2">
          <GitCompareArrows className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <h2 className="text-lg font-semibold">指标如何推动周期坐标</h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              每项贡献使用与周期判断完全相同的六个月共同篮子和有效权重；同步坐标决定四象限，领先坐标只影响确认速度。贡献是模型内算术分解，不表示现实经济因果。
            </p>
          </div>
        </div>
        <div className="mb-4 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
          <div className="rounded-lg border bg-muted/35 px-3 py-2 font-mono">水平缺口 = Σ（有效权重 × 近3月平均标准分 × 10）</div>
          <div className="rounded-lg border bg-muted/35 px-3 py-2 font-mono">动能 = Σ（有效权重 ×〔近3月 − 前3月〕平均标准分 × 10）</div>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <AxisDriverList
            title="同步水平由什么构成"
            description="水平贡献加总后，严格还原近三月同步活动相对历史中性 100 的缺口。"
            decomposition={latest.coincident_decomposition}
            metric="level"
            advisory={latest.coincident_basis_changed ? "本月同步共同篮子发生变化：分解算术仍成立，但本月暂停阶段判定。" : undefined}
          />
          <AxisDriverList
            title="同步动能为何改善或走弱"
            description="动能贡献比较同一批指标的近三月与此前三月，并严格还原纵轴读数。"
            decomposition={latest.coincident_decomposition}
            metric="momentum"
            advisory={latest.coincident_basis_changed ? "本月同步共同篮子发生变化：分解算术仍成立，但本月暂停阶段判定。" : undefined}
          />
          <AxisDriverList
            title="领先水平由什么构成"
            description="领先水平不直接决定阶段，只用于观察风险位于偏强还是偏弱一侧。"
            decomposition={latest.leading_decomposition}
            metric="level"
            limit={5}
            advisory={latest.leading_basis_changed ? "本月领先共同篮子发生变化：分解算术仍成立，但该方向不用于缩短确认期。" : undefined}
          />
          <AxisDriverList
            title="领先动能为何转向"
            description="领先动能只改变候选阶段的确认要求，不会单独生成阶段标签。"
            decomposition={latest.leading_decomposition}
            metric="momentum"
            limit={5}
            advisory={latest.leading_basis_changed ? "本月领先共同篮子发生变化：分解算术仍成立，但该方向不用于缩短确认期。" : undefined}
          />
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Gauge className="size-5" aria-hidden="true" />
                <CardTitle>绝对荣枯锚</CardTitle>
              </div>
              <Badge variant="outline">绝对景气：{ABSOLUTE_STATE_LABELS[latest.absolute_anchor.state]}</Badge>
            </div>
            <CardDescription>用5项PMI类绝对阈值约束相对周期的解释；这些锚不改变相对周期坐标。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-4 grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg bg-muted/55 p-3"><div className="text-xs text-muted-foreground">扩张广度</div><div className="mt-1 text-xl font-semibold tabular-nums">{breadth == null ? "--" : `${(breadth * 100).toFixed(0)}%`}</div></div>
              <div className="rounded-lg bg-muted/55 p-3"><div className="text-xs text-muted-foreground">平均阈值缺口</div><div className="mt-1 text-xl font-semibold tabular-nums">{signed(latest.absolute_anchor.gap, 2)}</div></div>
              <div className="rounded-lg bg-muted/55 p-3"><div className="text-xs text-muted-foreground">有效锚</div><div className="mt-1 text-xl font-semibold tabular-nums">{latest.absolute_anchor.valid_count}/{latest.absolute_anchor.total_count}</div></div>
            </div>
            {(latest.absolute_anchor.conflict || relativeImprovementAbsoluteWeakness) && (
              <div className="mb-4 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-950 dark:bg-amber-950/45 dark:text-amber-200">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                {relativeImprovementAbsoluteWeakness
                  ? "相对自身历史有所改善，但多数PMI锚仍低于50，不能解读为经济全面扩张。"
                  : "相对周期与绝对荣枯锚出现冲突，应分别阅读，不用单一标签覆盖两种信息。"}
              </div>
            )}
            <div className="grid gap-2 sm:grid-cols-2">
              {latest.absolute_anchor.anchors.map((anchor) => (
                <div key={anchor.code} className="rounded-lg border px-3 py-2.5">
                  <div className="flex items-start justify-between gap-2 text-xs">
                    <span className="font-medium">{anchor.name}</span>
                    <span className={anchor.state === "above" ? "text-sky-700 dark:text-sky-300" : anchor.state === "below" ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}>
                      {anchor.state === "above" ? "高于阈值" : anchor.state === "below" ? "低于阈值" : "暂无"}
                    </span>
                  </div>
                  <div className="mt-2 flex items-baseline justify-between gap-3 text-xs text-muted-foreground">
                    <span>三月均值 <strong className="font-medium text-foreground tabular-nums">{number(anchor.three_month_average, 2)}</strong></span>
                    <span>阈值 {number(anchor.threshold, 1)} · 缺口 {signed(anchor.gap, 2)}</span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <ThermometerSun className="size-5" aria-hidden="true" />
              <CardTitle>通胀环境</CardTitle>
            </div>
            <CardDescription>通胀是独立维度，不参与复苏、扩张、放缓、收缩四阶段坐标。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-xl bg-muted/55 p-4">
              <div className="text-xs text-muted-foreground">当前状态</div>
              <div className="mt-1 text-2xl font-semibold">{INFLATION_STATE_LABELS[latest.inflation.state]}</div>
              <div className="mt-1 text-xs text-muted-foreground">{INFLATION_DIRECTION_LABELS[latest.inflation.direction]}</div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg border p-3"><div className="text-muted-foreground">核心CPI三月均值</div><div className="mt-1 font-semibold tabular-nums">{latest.inflation.three_month_average == null ? "--" : `${number(latest.inflation.three_month_average, 2)}%`}</div></div>
              <div className="rounded-lg border p-3"><div className="text-muted-foreground">PPI三月均值</div><div className="mt-1 font-semibold tabular-nums">{latest.inflation.ppi_three_month_average == null ? "--" : `${number(latest.inflation.ppi_three_month_average, 2)}%`}</div></div>
            </div>
            <p className="text-xs leading-5 text-muted-foreground">{latest.inflation.rationale}</p>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <GitCompareArrows className="size-5" aria-hidden="true" />
            <CardTitle>什么会改变判断</CardTitle>
          </div>
          <CardDescription>这里列规则和观察条件，不输出伪精确的阶段转换概率或日期。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div>
            <div className="mb-2 text-xs font-medium text-muted-foreground">当前最需要观察</div>
            <ul className="space-y-2">
              {latest.triggers.map((trigger) => (
                <li key={trigger} className="flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2 text-xs leading-5">
                  <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  {trigger}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="mb-2 text-xs font-medium text-muted-foreground">阶段切换规则</div>
            <ul className="space-y-2">
              {data.change_conditions.map((condition) => (
                <li key={condition} className="flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-5">
                  <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  {condition}
                </li>
              ))}
            </ul>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Database className="size-5" aria-hidden="true" />
            <CardTitle>方法与边界</CardTitle>
          </div>
          <CardDescription>先看可复核规则，再看模型标签。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">相对周期</div><p className="mt-1 leading-5 text-muted-foreground">四象限基于同步活动相对自身历史的水平与三月动能，不是官方衰退认定。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">连续确认</div><p className="mt-1 leading-5 text-muted-foreground">候选阶段必须连续满足规则才确认；死区内沿用已有阶段，避免频繁跳变。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">六个月平衡面板</div><p className="mt-1 leading-5 text-muted-foreground">最近三个月与此前三个月只用共同信号和同一权重重算；共同篮子变化月展示数值，并重置尚未确认的候选。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">当前快照</div><p className="mt-1 leading-5 text-muted-foreground">本页使用最新/最终数据。历史判断对数据修订是否稳定，请到伪实时回测中核验。</p></div>
        </CardContent>
      </Card>

      <div className="space-y-3 rounded-xl border border-dashed p-5 text-xs leading-6 text-muted-foreground">
        <div className="flex items-center gap-2 font-medium text-foreground">
          <Info className="size-4" aria-hidden="true" />
          模型说明
        </div>
        <p>周期定位方法 v{data.methodology_version} · 活动矩阵方法 v{data.a1_methodology_version ?? "--"} · 数据口径 {data.data_basis === "final" ? "最新/最终快照" : data.data_basis}</p>
        {data.warnings.map((warning) => <p key={warning}>• {warning}</p>)}
        {data.a1_warnings.length > 0 && (
          <details className="rounded-lg border px-3 py-2">
            <summary className="cursor-pointer font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">查看活动矩阵的数据边界</summary>
            <div className="mt-2 space-y-1">{data.a1_warnings.map((warning) => <p key={warning}>• {warning}</p>)}</div>
          </details>
        )}
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <Link href="/analysis/cn/business-cycle/backtest" className="inline-flex items-center gap-1 font-medium text-foreground hover:underline">
            检验历史当时判断 <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
          <Link href="/analysis/cn/activity-matrix" className="inline-flex items-center gap-1 font-medium text-foreground hover:underline">
            下钻到A1月度活动矩阵 <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
        </div>
      </div>
    </div>
  );
}
