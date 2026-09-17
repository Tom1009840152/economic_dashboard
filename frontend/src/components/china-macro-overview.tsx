import {
  Activity,
  AlertTriangle,
  CalendarClock,
  GitBranch,
  Radar,
  ThermometerSun,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { phasePlainLabel } from "@/lib/china-business-cycle-presentation";
import type {
  ActivityMatrixDashboard,
  BusinessCyclePhase,
  BusinessCycleRegimeDashboard,
  MonetaryTransmissionDashboard,
} from "@/lib/api";

const PHASE_LABELS: Record<BusinessCyclePhase, string> = {
  recovery: "复苏",
  expansion: "扩张",
  slowdown: "放缓",
  contraction: "收缩",
};

const INFLATION_LABELS = {
  deflation_pressure: "通缩压力",
  low_inflation: "低通胀",
  moderate: "温和通胀",
  heating: "通胀升温",
  unavailable: "证据不足",
};

const INFLATION_DIRECTION_LABELS = {
  reflation: "方向回升",
  disinflation: "方向回落",
  stable: "方向平稳",
  unavailable: "方向暂不可判",
};

function signalByKey(data: MonetaryTransmissionDashboard | null, key: string) {
  return data?.signals.find((signal) => signal.key === key);
}

function MetricTile({
  icon: Icon,
  label,
  value,
  detail,
  period,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
  period: string | null | undefined;
}) {
  return (
    <div className="rounded-xl border bg-background/80 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Icon className="size-4" aria-hidden="true" />
          {label}
        </div>
        <span className="text-xs tabular-nums text-muted-foreground">{period ?? "暂无日期"}</span>
      </div>
      <div className="mt-3 text-lg font-semibold">{value}</div>
      <p className="mt-1 text-sm leading-5 text-muted-foreground">{detail}</p>
    </div>
  );
}

export function ChinaMacroOverview({
  businessCycle,
  businessCycleFailed,
  activityMatrix,
  activityMatrixFailed,
  monetaryTransmission,
  monetaryTransmissionFailed,
  unavailableModules,
}: {
  businessCycle: BusinessCycleRegimeDashboard | null;
  businessCycleFailed: boolean;
  activityMatrix: ActivityMatrixDashboard | null;
  activityMatrixFailed: boolean;
  monetaryTransmission: MonetaryTransmissionDashboard | null;
  monetaryTransmissionFailed: boolean;
  unavailableModules: string[];
}) {
  const cycle = businessCycle?.latest;
  const matrix = activityMatrix?.latest;
  const credit = signalByKey(monetaryTransmission, "credit_impulse");
  const activePhase = cycle?.phase_basis === "active_decision"
    ? cycle.confirmed_phase ?? cycle.phase
    : null;

  const growthValue = businessCycleFailed
    ? "暂不可用"
    : cycle == null
      ? "证据不足"
      : activePhase == null
        ? "本月暂不可判"
        : phasePlainLabel(activePhase);
  const growthDetail = businessCycleFailed
    ? "经济周期模块本次加载失败，不据此推断增长状态。"
    : cycle == null
      ? "当前覆盖和连续性尚未形成有效周期判断。"
      : activePhase != null
        ? cycle.candidate_phase
          ? `已确认${PHASE_LABELS[activePhase]}；正在观察向${PHASE_LABELS[cycle.candidate_phase]}切换，连续 ${cycle.candidate_streak}/${cycle.required_confirmation_months ?? "--"} 个月。`
          : `当前有效阶段为${PHASE_LABELS[activePhase]}，本月满足模型判定条件。`
        : cycle.confirmed_phase
          ? `最近确认${PHASE_LABELS[cycle.confirmed_phase]}，最近可判定月 ${cycle.last_decision_period ?? businessCycle?.last_decision_period ?? "暂无"}；仅作历史参考。`
          : cycle.candidate_phase
            ? `${PHASE_LABELS[cycle.candidate_phase]}为候选方向，尚未完成连续确认。`
            : "当前尚无可沿用的已确认阶段。";

  const inflationValue = businessCycleFailed
    ? "暂不可用"
    : cycle == null
      ? "证据不足"
      : `${INFLATION_LABELS[cycle.inflation.state]} · ${INFLATION_DIRECTION_LABELS[cycle.inflation.direction]}`;
  const inflationDetail = businessCycleFailed
    ? "通胀判断随经济周期模块一并加载，当前不把失败解释为中性。"
    : cycle?.inflation.rationale ?? "核心价格数据暂不足。";

  const creditValue = monetaryTransmissionFailed
    ? "暂不可用"
    : credit?.state ?? "证据不足";
  const creditDetail = monetaryTransmissionFailed
    ? "货币与信用传导模块本次加载失败，不据此推断信用状态。"
    : credit
      ? `${credit.interpretation} 信用脉冲 ${credit.value > 0 ? "+" : ""}${credit.value.toFixed(2)} ${credit.unit}。`
      : "信用传导模块未返回信用脉冲信号。";

  const allModulesFailed = businessCycleFailed && activityMatrixFailed && monetaryTransmissionFailed;
  const headline = allModulesFailed
    ? "当前无法形成增长—通胀—信用总判断"
    : `增长：${growthValue}；通胀：${inflationValue}；信用：${creditValue}`;

  const activityEvidence = activityMatrixFailed
    ? "活动矩阵本次加载失败，暂不能补充增长证据。"
    : matrix == null
      ? "活动矩阵证据不足，暂未形成同步与领先读数。"
      : `同步 ${matrix.coincident_index?.toFixed(1) ?? "--"} · 领先 ${matrix.leading_index?.toFixed(1) ?? "--"}（历史中性=100） · 总体覆盖 ${(matrix.overall_coverage * 100).toFixed(0)}% · 实时覆盖 ${(matrix.realtime_coverage * 100).toFixed(0)}%。`;

  return (
    <Card className="overflow-hidden border-foreground/25 shadow-sm">
      <CardHeader className="gap-4 bg-foreground text-background sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-4xl">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium tracking-[0.18em] opacity-70">
            <Activity className="size-4" aria-hidden="true" />
            中国宏观简报
          </div>
          <CardTitle className="text-2xl leading-tight sm:text-3xl">
            {headline}
          </CardTitle>
          <p className="mt-3 text-sm leading-6 opacity-75">
            三项结论分别沿用各自模型与观察期；活动矩阵只补充增长证据，不额外加权打分。
          </p>
        </div>
        <Badge variant="outline" className="w-fit shrink-0 border-background/30 bg-background/10 text-background">
          分项判断 · 不合成总分
        </Badge>
      </CardHeader>

      <CardContent className="p-4 sm:p-5">
        <div className="grid gap-3 lg:grid-cols-3">
          <MetricTile
            icon={Activity}
            label="增长周期"
            value={growthValue}
            detail={growthDetail}
            period={cycle?.period}
          />
          <MetricTile
            icon={ThermometerSun}
            label="通胀环境"
            value={inflationValue}
            detail={inflationDetail}
            period={cycle?.inflation.period}
          />
          <MetricTile
            icon={GitBranch}
            label="信用传导"
            value={creditValue}
            detail={creditDetail}
            period={credit?.period}
          />
        </div>

        <div className="mt-3 flex items-start gap-3 rounded-xl border bg-muted/35 px-4 py-3 text-sm leading-6">
          <Radar className="mt-1 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div>
            <span className="font-medium">增长补充证据</span>
            <span className="ml-2 text-muted-foreground">{activityEvidence}</span>
            {matrix?.period && <span className="ml-2 text-xs text-muted-foreground">观察期 {matrix.period}</span>}
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-2 border-t pt-4 text-sm text-muted-foreground lg:flex-row lg:items-center lg:justify-between">
          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
            <CalendarClock className="size-4 shrink-0" aria-hidden="true" />
            增长 {cycle?.period ?? "--"} · 通胀 {cycle?.inflation.period ?? "--"} · 活动 {matrix?.period ?? "--"} · 信用 {credit?.period ?? "--"} · 政策核验 {monetaryTransmission?.freshness?.policy_rate_verified_through ?? "--"}
          </span>
          <span>不同截止日期代表真实发布节奏，不强行对齐成同一“当前值”。</span>
        </div>

        {unavailableModules.length > 0 && (
          <div
            className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
            role="alert"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>
              本次加载失败：{unavailableModules.join("、")}。加载失败不表示没有信号或数值为零；其余结论仍按各自观察期展示。
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
