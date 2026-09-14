import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Gauge,
  Route,
  ThermometerSun,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  BusinessCyclePhase,
  BusinessCyclePhaseStatus,
  BusinessCycleRegimeDashboard,
} from "@/lib/api";

const PHASE_LABELS: Record<BusinessCyclePhase, string> = {
  recovery: "复苏",
  expansion: "扩张",
  slowdown: "放缓",
  contraction: "收缩",
};

const STATUS_LABELS: Record<BusinessCyclePhaseStatus, string> = {
  confirmed: "已确认",
  candidate: "候选 · 待连续确认",
  transition: "切换观察",
  held_uncomparable: "口径变动 · 沿用上期判断",
  stale: "连续不可比 · 暂不更新",
  insufficient: "证据不足",
};

const CONFIDENCE_LABELS = {
  high: "高",
  medium: "中等",
  low: "偏低",
  insufficient: "不足",
};

const LEADING_LABELS = {
  up: "领先信号向上",
  down: "领先信号转弱",
  neutral: "领先方向中性",
  unavailable: "领先信号不足",
};

const INFLATION_LABELS = {
  deflation_pressure: "通缩压力",
  low_inflation: "低通胀",
  moderate: "温和通胀",
  heating: "通胀升温",
  unavailable: "通胀数据不足",
};

function phaseName(phase: BusinessCyclePhase | null): string {
  return phase ? PHASE_LABELS[phase] : "暂未定位";
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

export function ChinaBusinessCycleSummaryCard({
  data,
}: {
  data: BusinessCycleRegimeDashboard;
}) {
  const latest = data.latest;

  return (
    <Link
      href="/analysis/cn/business-cycle"
      className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      aria-label="查看中国相对增长周期定位详情"
    >
      <Card className="group overflow-hidden border-foreground/20 bg-gradient-to-br from-background via-background to-muted/65 transition-all hover:border-foreground/35 hover:shadow-md">
        <CardHeader className="gap-4 pb-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">
              <Route className="size-4" aria-hidden="true" />
              中国相对增长周期
            </div>
            <CardTitle className="text-xl">经济周期定位</CardTitle>
          </div>
          {latest && (
            <Badge variant="outline" className={statusClass(latest.phase_status)}>
              {STATUS_LABELS[latest.phase_status]}
            </Badge>
          )}
        </CardHeader>

        <CardContent>
          {latest ? (
            <>
              <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr]">
                <div>
                  <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
                    <div className="text-4xl font-semibold tracking-tight sm:text-5xl">
                      {latest.phase_label || phaseName(latest.phase)}
                      {(latest.phase_status === "held_uncomparable" || latest.phase_status === "stale") && (
                        <span className="ml-2 text-base font-medium text-muted-foreground sm:text-lg">（沿用）</span>
                      )}
                    </div>
                    <div className="pb-1 text-xs text-muted-foreground">
                      {latest.duration_months == null ? "持续时间待确认" : `自模型确认起已沿用 ${latest.duration_months} 个月`}
                    </div>
                  </div>
                  <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground">
                    {latest.summary}
                  </p>
                  {!latest.decision_eligible && latest.decision_reasons.length > 0 && (
                    <div className="mt-3 rounded-lg border border-dashed px-3 py-2 text-xs leading-5 text-muted-foreground">
                      最近可判定月：{data.last_decision_period ?? "暂无"}；已连续 {latest.undecidable_streak} 个月未更新判断。
                      <span className="mt-1 block">原因：{latest.decision_reasons[0]}</span>
                    </div>
                  )}
                  {latest.candidate_phase && (
                    <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                      {latest.phase_status === "transition" ? "当前确认阶段仍沿用；候选方向：" : "阶段候选："}
                      {phaseName(latest.candidate_phase)}，已连续 {latest.candidate_streak}
                      {latest.required_confirmation_months == null ? "" : `/${latest.required_confirmation_months}`} 个月；尚未确认切换。
                    </div>
                  )}
                </div>

                <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-1">
                  <div className="rounded-lg border bg-background/70 px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <CircleDashed className="size-3.5" aria-hidden="true" />
                      领先展望
                    </div>
                    <div className="mt-1 font-medium">{LEADING_LABELS[latest.leading_direction]}</div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">{latest.outlook}</div>
                  </div>
                  <div className="rounded-lg border bg-background/70 px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <ThermometerSun className="size-3.5" aria-hidden="true" />
                      通胀环境
                    </div>
                    <div className="mt-1 font-medium">{INFLATION_LABELS[latest.inflation.state]}</div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">核心CPI三月均值 {latest.inflation.three_month_average == null ? "--" : `${latest.inflation.three_month_average.toFixed(2)}%`}</div>
                  </div>
                  <div className="rounded-lg border bg-background/70 px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <Gauge className="size-3.5" aria-hidden="true" />
                      判断置信度
                    </div>
                    <div className="mt-1 font-medium">{CONFIDENCE_LABELS[latest.confidence]}</div>
                    <div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                      {latest.phase_status === "confirmed" ? <CheckCircle2 className="size-3" aria-hidden="true" /> : <CircleDashed className="size-3" aria-hidden="true" />}
                      {latest.phase_status === "confirmed"
                        ? "当前阶段已满足确认规则"
                        : latest.phase_status === "transition"
                          ? "原阶段有效，候选正在累计"
                          : "暂不触发阶段切换"}
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-4 flex flex-col gap-2 border-t pt-4 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
                <span>数据截至 {latest.period} · 最近可判定 {data.last_decision_period ?? "暂无"} · 不等同于GDP衰退定义</span>
                <span className="inline-flex items-center gap-1 font-medium text-foreground">
                  查看罗盘与判断依据
                  <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                </span>
              </div>
            </>
          ) : (
            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">
              当前数据不足以定位相对增长周期；活动矩阵仍可用于检查具体数据缺口。
            </div>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
