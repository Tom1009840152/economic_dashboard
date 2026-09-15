import type React from "react";
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Database,
  FileClock,
  GitCompareArrows,
  History,
  Info,
  Layers3,
  ShieldCheck,
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
import type {
  BusinessCycleBacktestDashboard,
  BusinessCycleBacktestMonth,
  BusinessCycleBacktestRegimeSnapshot,
  BusinessCyclePhase,
} from "@/lib/api";

const PHASE_LABELS: Record<BusinessCyclePhase, string> = {
  recovery: "复苏",
  expansion: "扩张",
  slowdown: "放缓",
  contraction: "收缩",
};

const REASON_LABELS: Record<string, string> = {
  missing_available_at: "缺少可追溯的发布时间",
  missing_vintage_history: "缺少历史版本记录",
  ambiguous_revision: "修订版本无法唯一还原",
  ambiguous_revisions: "修订版本无法唯一还原",
  insufficient_input_coverage: "当时可见指标覆盖不足",
  insufficient_history: "当时时点的历史长度不足",
  realtime_unclassified: "当时信息无法形成阶段判断",
  final_unclassified: "事后参考无法形成阶段判断",
  both_unclassified: "两套信息都无法形成阶段判断",
  unavailable: "严格信息集不可用",
  "available_at不足": "缺少可追溯发布时间",
  "A1覆盖不足": "当时指标覆盖不足，无法计算活动矩阵",
  "warmup不足": "当时时点的连续历史长度不足",
  "A2无可展示阶段": "当时无法形成可展示的阶段标签",
  "A2无confirmed phase": "当时尚无已确认阶段",
  "A2当月不可决策": "当月未达到正式判定条件",
  "final同月缺失": "事后参考缺少同月结果",
  "公式版本不合格": "当时可见数据的公式版本不合格",
  no_known_available_at: "缺少可追溯发布时间",
  a1_coverage_insufficient: "当时指标覆盖不足，无法计算活动矩阵",
  warmup_insufficient: "当时时点的连续历史长度不足",
  a2_display_phase_unavailable: "当时无法形成可展示的阶段标签",
  a2_confirmed_phase_unavailable: "当时尚无已确认阶段",
  a2_decision_ineligible: "当月未达到正式判定条件",
  final_same_month_unavailable: "事后参考缺少同月结果",
  formula_version_ineligible: "当时可见数据的公式版本不合格",
};

const STATUS_COPY = {
  ok: {
    badge: "样本达到展示门槛",
    title: "严格回测已有可比较样本",
    className:
      "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/45 dark:text-emerald-200",
  },
  limited: {
    badge: "样本有限",
    title: "已有回测案例，但还不足以下稳定性结论",
    className:
      "border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/45 dark:text-amber-200",
  },
  unavailable: {
    badge: "暂不可评分",
    title: "当前最可信的结论，是历史版本数据还不够完整",
    className:
      "border-slate-200 bg-slate-50 text-slate-900 dark:border-slate-800 dark:bg-slate-950/55 dark:text-slate-200",
  },
};

function phaseName(phase: BusinessCyclePhase | null | undefined): string {
  return phase ? PHASE_LABELS[phase] : "不可判断";
}

function percent(value: number | null | undefined): string {
  return value == null ? "暂不可评估" : `${(value * 100).toFixed(0)}%`;
}

function signedMonth(value: number | null | undefined): string {
  if (value == null) return "暂不可评估";
  if (value === 0) return "同期确认";
  return value > 0 ? `晚 ${value} 个月` : `早 ${Math.abs(value)} 个月`;
}

function decisionTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(parsed);
}

function reasonName(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replaceAll("_", " ");
}

function roleName(role: string): string {
  if (role === "coincident") return "同步指标";
  if (role === "leading") return "领先指标";
  if (role === "coincident+leading") return "同步与领先共用";
  if (role === "inflation") return "通胀环境";
  if (role === "validation") return "外部验证";
  return "模型输入";
}

function phaseClass(phase: BusinessCyclePhase | null | undefined): string {
  if (phase === "recovery") return "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-200";
  if (phase === "expansion") return "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200";
  if (phase === "slowdown") return "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200";
  if (phase === "contraction") return "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-200";
  return "border-border bg-muted/60 text-muted-foreground";
}

function SnapshotCell({ snapshot }: { snapshot: BusinessCycleBacktestRegimeSnapshot | null }) {
  if (!snapshot) {
    return <span className="text-xs text-muted-foreground">无可用结果</span>;
  }

  const displayPhase = snapshot.confirmed_phase ?? snapshot.phase;
  const basisLabel = snapshot.phase_basis === "active_decision"
    ? snapshot.phase_status === "transition" ? "本月可判 · 切换观察" : "本月可判"
    : snapshot.phase_basis === "carried_forward"
      ? "沿用旧判断"
      : snapshot.phase_basis === "pending_confirmation"
        ? "形成候选 · 尚未确认"
        : "本月不可判";

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className={phaseClass(displayPhase)}>
          {phaseName(displayPhase)}
        </Badge>
        <span className="text-[11px] font-medium text-foreground">{basisLabel}</span>
      </div>
      {snapshot.phase_basis === "carried_forward" && (
        <div className="mt-1 text-[10px] leading-4 text-muted-foreground">
          <span className="block">上次确认：{phaseName(snapshot.confirmed_phase)}{snapshot.confirmed_since ? ` · ${snapshot.confirmed_since}` : ""}</span>
          <span className="block">最近可判 {snapshot.last_decision_period ?? "暂无"} · 连续 {snapshot.carry_forward_months} 个月未更新</span>
        </div>
      )}
    </div>
  );
}

function comparisonLabel(month: BusinessCycleBacktestMonth): string {
  const realtimeBasis = month.realtime?.phase_basis;
  const finalBasis = month.final?.phase_basis;
  const bothActive = realtimeBasis === "active_decision" && finalBasis === "active_decision";
  const bothCarried = realtimeBasis === "carried_forward" && finalBasis === "carried_forward";
  const bothPending = realtimeBasis === "pending_confirmation" && finalBasis === "pending_confirmation";
  if (month.comparison_type === "same") {
    if (bothActive) return "主动判断一致";
    if (bothCarried) return "沿用标签一致";
    if (bothPending) return "候选方向一致 · 均待确认";
    return "标签一致 · 判断口径不同";
  }
  if (month.comparison_type === "phase_changed") {
    if (bothActive) return "主动判断翻转";
    if (bothCarried) return "沿用标签翻转";
    if (bothPending) return "候选方向不同 · 均待确认";
    return "标签翻转 · 判断口径不同";
  }
  if (month.comparison_type === "realtime_unclassified") return "当时未形成标签";
  if (month.comparison_type === "final_unclassified") return "事后参考未形成标签";
  if (month.comparison_type === "both_unclassified") return "两边均未形成标签";
  return "不可比较";
}

function comparisonClass(month: BusinessCycleBacktestMonth): string {
  const realtimeBasis = month.realtime?.phase_basis;
  const finalBasis = month.final?.phase_basis;
  if (realtimeBasis !== finalBasis && month.comparable) return "text-amber-700 dark:text-amber-300";
  if (realtimeBasis === "carried_forward" && finalBasis === "carried_forward") return "text-muted-foreground";
  if (month.comparison_type === "same") return "text-emerald-700 dark:text-emerald-300";
  if (month.comparison_type === "phase_changed") return "text-amber-700 dark:text-amber-300";
  return "text-muted-foreground";
}

function DefinitionCard({
  icon,
  eyebrow,
  title,
  children,
}: {
  icon: React.ReactNode;
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border bg-background/75 p-4">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {icon}
        {eyebrow}
      </div>
      <div className="mt-2 text-base font-semibold">{title}</div>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{children}</p>
    </div>
  );
}

export function ChinaCycleBacktestDetail({ data }: { data: BusinessCycleBacktestDashboard }) {
  const coverage = data.coverage;
  const stability = data.stability;
  const activeStatus = stability.decision_comparable_months >= stability.minimum_rate_sample
    ? "ok"
    : stability.decision_comparable_months > 0
      ? "limited"
      : "unavailable";
  const copy = STATUS_COPY[activeStatus];
  const enoughPhaseSample = stability.comparable_months >= stability.minimum_rate_sample;
  const enoughActiveSample = stability.decision_comparable_months >= stability.minimum_rate_sample;
  const enoughCarriedSample = stability.carried_forward_comparable_months >= stability.minimum_rate_sample;
  const enoughTransitions = data.transitions.matched_count >= data.transitions.minimum_lag_sample;
  const recentMonths = data.months.slice(-18).reverse();
  const reasons = Object.entries(coverage.reason_counts).sort((left, right) => right[1] - left[1]);
  const maxReasonCount = Math.max(1, ...reasons.map(([, count]) => count));
  const readiness = [...data.input_readiness]
    .sort((left, right) => (left.on_schedule_rate ?? -1) - (right.on_schedule_rate ?? -1))
    .slice(0, 10);
  const fullRobustness = data.robustness.full_sample;
  const exCovidRobustness = data.robustness.exclude_covid_2020;
  const robustnessReady =
    fullRobustness.comparable_months >= fullRobustness.minimum_rate_sample &&
    exCovidRobustness.comparable_months >= exCovidRobustness.minimum_rate_sample &&
    fullRobustness.agreement_rate != null &&
    exCovidRobustness.agreement_rate != null;

  return (
    <div className="space-y-8">
      <Card className="overflow-hidden border-foreground/20 bg-gradient-to-br from-background via-background to-muted/70">
        <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">
              <ShieldCheck className="size-4" aria-hidden="true" />
              严格历史信息集
            </div>
            <CardTitle className="max-w-3xl text-2xl sm:text-3xl">{copy.title}</CardTitle>
            <CardDescription className="mt-3 max-w-3xl text-sm leading-6">{data.summary}</CardDescription>
          </div>
          <Badge variant="outline" className={copy.className}>{copy.badge}</Badge>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">计划检查月份</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">{coverage.scheduled_months}</div>
              <div className="mt-1 text-xs text-muted-foreground">{data.start_period ?? "--"} 至 {data.end_period ?? "--"}</div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">主动判断可比月</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">{stability.decision_comparable_months}/{coverage.scheduled_months}</div>
              <div className="mt-1 text-xs text-muted-foreground">两边当月都可判且已有确认阶段</div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">主动判断一致率</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">
                {stability.decision_comparable_months === 0
                  ? "暂不可评估"
                  : enoughActiveSample
                    ? percent(stability.decision_agreement_rate)
                    : `${stability.decision_agreement_count ?? 0} 个月一致`}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {stability.decision_comparable_months === 0
                  ? "暂无主动判断可比月"
                  : enoughActiveSample
                    ? `基于 ${stability.decision_comparable_months} 个主动判断月`
                    : `少于${stability.minimum_rate_sample}个可比月，不展示比例`}
              </div>
            </div>
            <div className="rounded-xl border bg-background/75 p-4">
              <div className="text-xs text-muted-foreground">含沿用标签一致率</div>
              <div className="mt-2 text-2xl font-semibold tabular-nums">
                {stability.comparable_months === 0
                  ? "暂不可评估"
                  : enoughPhaseSample
                    ? percent(stability.agreement_rate)
                    : `${stability.agreement_count ?? 0} 个月一致`}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">包含历史状态沿用，仅作辅助口径</div>
            </div>
          </div>

          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-dashed px-3 py-2.5">
              <div className="text-[11px] text-muted-foreground">双方均沿用旧判断</div>
              <div className="mt-1 text-sm font-semibold tabular-nums">
                {stability.carried_forward_comparable_months} 个月
                {enoughCarriedSample && stability.carried_forward_agreement_rate != null
                  ? ` · 一致率 ${percent(stability.carried_forward_agreement_rate)}`
                  : ""}
              </div>
              {!enoughCarriedSample && stability.carried_forward_comparable_months > 0 && (
                <div className="mt-1 text-[10px] text-muted-foreground">
                  {stability.carried_forward_agreement_count ?? 0} 个月标签一致；样本不足，不展示比例
                </div>
              )}
            </div>
            <div className="rounded-lg border border-dashed px-3 py-2.5">
              <div className="text-[11px] text-muted-foreground">一边主动、一边沿用</div>
              <div className="mt-1 text-sm font-semibold tabular-nums">{stability.mixed_basis_comparable_months} 个月</div>
              <div className="mt-1 text-[10px] text-muted-foreground">判断基础不同，不并入主动判断稳定率</div>
            </div>
            <div className="rounded-lg border border-dashed px-3 py-2.5">
              <div className="text-[11px] text-muted-foreground">等待连续确认</div>
              <div className="mt-1 text-sm font-semibold tabular-nums">{stability.pending_confirmation_comparable_months} 个月</div>
              <div className="mt-1 text-[10px] text-muted-foreground">候选不冒充已确认阶段</div>
            </div>
          </div>

          {activeStatus !== "ok" && (
            <div className={`mt-4 flex items-start gap-2 rounded-xl border px-4 py-3 text-sm leading-6 ${copy.className}`}>
              <TriangleAlert className="mt-1 size-4 shrink-0" aria-hidden="true" />
              <div>
                <div className="font-medium">
                  {enoughPhaseSample
                    ? "主动判断样本有限，不能只看含沿用标签的总样本"
                    : "不能把历史现场不足直接解读为模型失效"}
                </div>
                <p className="mt-0.5 text-xs leading-5 opacity-90">
                  {enoughPhaseSample
                    ? "含沿用标签的月份虽已足够，但双方当月都能主动判断的月份仍偏少；口径不可比、旧阶段沿用和等待确认都不会冒充主动样本。"
                    : "它表示数据库目前无法严格还原足够多的历史决策现场。继续回填发布时间和历史版本后，统计结论才会逐步开放。"}
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <section className="grid gap-4 md:grid-cols-[1fr_auto_1fr] md:items-stretch">
        <DefinitionCard icon={<Clock3 className="size-4" aria-hidden="true" />} eyebrow="当时判断" title="只看当时真正能看到的数据">
          观察月为 t，在次月20日18:00（北京时间）模拟决策；该时点之后发布或修订的数据一律不能进入计算。
        </DefinitionCard>
        <div className="hidden items-center justify-center text-muted-foreground md:flex">
          <ArrowRight className="size-5" aria-hidden="true" />
        </div>
        <DefinitionCard icon={<Database className="size-4" aria-hidden="true" />} eyebrow="事后参考" title="用今天完整快照重算同一个月">
          两边比较的是同一观察月。事后参考也不是真实经济的标准答案，更不是官方周期认定。
        </DefinitionCard>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <FileClock className="size-5" aria-hidden="true" />
              <CardTitle>样本覆盖与缺口</CardTitle>
            </div>
            <CardDescription>先确认有多少历史现场能够被严格还原，再讨论一致或翻转；同一个月可能同时命中多个缺口原因。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-xl bg-muted/55 p-4">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="font-medium">含沿用标签可比月</span>
                <span className="tabular-nums text-muted-foreground">{coverage.display_evaluable_months}/{coverage.scheduled_months}</span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-background ring-1 ring-foreground/10">
                <div
                  className="h-full rounded-full bg-sky-600"
                  style={{ width: `${Math.max(0, Math.min(coverage.display_evaluable_rate ?? 0, 1)) * 100}%` }}
                />
              </div>
              <div className="mt-2 text-xs text-muted-foreground">
                其中两边都能主动判断 {coverage.decision_evaluable_months} 个月；另有 {coverage.unavailable_months} 个月没有可比较的页面标签。
              </div>
            </div>

            <div className="mt-3 rounded-lg border border-dashed px-3 py-2 text-xs leading-5 text-muted-foreground">
              <span className="font-medium text-foreground">两层口径：</span>
              “含沿用标签”会保留最近一次确认结果；“主动判断”要求当月两边都满足判定条件，且已有确认阶段。
              {stability.decision_comparable_months === 0
                ? " 当前没有主动判断可比样本，因此主动判断一致性暂不可评估。"
                : stability.decision_comparable_months >= stability.minimum_rate_sample && stability.decision_agreement_rate != null
                  ? ` 当前有 ${stability.decision_comparable_months} 个主动判断可比月，一致率为 ${percent(stability.decision_agreement_rate)}。`
                  : ` 当前有 ${stability.decision_comparable_months} 个主动判断可比月，样本未达比例展示门槛。`}
            </div>

            <div className="mt-4 space-y-3">
              {reasons.length > 0 ? reasons.map(([reason, count]) => (
                <div key={reason}>
                  <div className="mb-1 flex items-center justify-between gap-3 text-xs">
                    <span>{reasonName(reason)}</span>
                    <span className="tabular-nums text-muted-foreground">{count}个月</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-amber-500/75" style={{ width: `${(count / maxReasonCount) * 100}%` }} />
                  </div>
                </div>
              )) : (
                <div className="rounded-lg border border-dashed p-4 text-xs text-muted-foreground">当前没有记录到样本排除原因。</div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Layers3 className="size-5" aria-hidden="true" />
              <CardTitle>最需要补齐的数据履历</CardTitle>
            </div>
            <CardDescription>优先列出固定决策时点就绪率最低的输入：按模型实际使用月份检查，数据必须在该月的次月20日18:00前已经发布。</CardDescription>
          </CardHeader>
          <CardContent>
            {readiness.length > 0 ? (
              <div className="space-y-2">
                {readiness.map((item) => (
                  <div key={`${item.role}-${item.code}`} className="rounded-lg border px-3 py-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-medium">{item.name}</div>
                        <div className="mt-0.5 text-[11px] text-muted-foreground">
                          {roleName(item.role)} · {item.code}
                          {item.observation_lag_months > 0 ? ` · 源数据固定滞后${item.observation_lag_months}个月使用` : ""}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-sm font-semibold tabular-nums">{percent(item.on_schedule_rate)}</div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground">决策时点就绪</div>
                      </div>
                    </div>
                    <div className="mt-2 text-[11px] leading-5 text-muted-foreground">
                      <span className="block">按模型使用时点可见 {item.on_schedule_observations}/{item.final_observations} · 已知发布时间 {item.known_available_at_observations}/{item.final_observations} · 未知发布时间 {item.unknown_available_at_observations}</span>
                      <span className="block">整组不可还原 {item.non_reconstructable_observations} · 同时戳歧义 {item.ambiguous_revision_observations} · 修订发布时间未知 {item.unknown_revision_observations}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">暂无输入准备度审计结果。</div>
            )}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <History className="size-5" aria-hidden="true" />
              <CardTitle>逐月对照</CardTitle>
            </div>
            <Badge variant="outline">最近 {recentMonths.length} 期</Badge>
          </div>
          <CardDescription>横向比较同一观察月的“当时判断”和“事后参考”，并明确区分本月主动判断与历史状态沿用。</CardDescription>
        </CardHeader>
        <CardContent>
          {recentMonths.length > 0 ? (
            <div className="space-y-2">
              <div className="hidden grid-cols-[7rem_1fr_1.5rem_1fr_8rem] gap-3 px-3 text-[11px] font-medium text-muted-foreground sm:grid">
                <span>观察月</span><span>当时判断</span><span /><span>事后参考</span><span>比较结果</span>
              </div>
              {recentMonths.map((month) => (
                <div key={`${month.observation_period}-${month.decision_as_of}`} className="rounded-xl border px-3 py-3">
                  <div className="grid gap-2 sm:grid-cols-[7rem_1fr_1.5rem_1fr_8rem] sm:items-center sm:gap-3">
                    <div>
                      <div className="text-xs font-medium tabular-nums">{month.observation_period}</div>
                      <div className="mt-0.5 text-[10px] text-muted-foreground">决策时点 {decisionTime(month.decision_as_of)}</div>
                    </div>
                    <div className="grid min-w-0 grid-cols-[3rem_minmax(0,1fr)] items-start gap-2 sm:block">
                      <span className="text-[11px] text-muted-foreground sm:hidden">当时</span>
                      <SnapshotCell snapshot={month.realtime} />
                    </div>
                    <ArrowRight className="hidden size-3.5 text-muted-foreground sm:block" aria-hidden="true" />
                    <div className="grid min-w-0 grid-cols-[3rem_minmax(0,1fr)] items-start gap-2 sm:block">
                      <span className="text-[11px] text-muted-foreground sm:hidden">事后</span>
                      <SnapshotCell snapshot={month.final} />
                    </div>
                    <div className={`text-xs font-medium ${comparisonClass(month)}`}>{comparisonLabel(month)}</div>
                  </div>
                  {!month.comparable && month.exclusion_reasons.length > 0 && (
                    <div className="mt-2 border-t pt-2 text-[11px] leading-5 text-muted-foreground">
                      未纳入比较：{month.exclusion_reasons.map(reasonName).join("；")}
                    </div>
                  )}
                </div>
              ))}
              {data.months.length > recentMonths.length && (
                <p className="pt-2 text-center text-xs text-muted-foreground">本页聚焦最近时期；统计仍使用完整回测区间。</p>
              )}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">当前回测区间没有可展示月份。</div>
          )}
        </CardContent>
      </Card>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CalendarClock className="size-5" aria-hidden="true" />
              <CardTitle>阶段切换确认滞后</CardTitle>
            </div>
            <CardDescription>比较的是模型标签的确认时间，不是现实经济拐点的预测误差。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-lg bg-muted/55 p-3"><div className="text-[11px] text-muted-foreground">已匹配切换</div><div className="mt-1 text-xl font-semibold tabular-nums">{data.transitions.matched_count}</div></div>
              <div className="rounded-lg bg-muted/55 p-3"><div className="text-[11px] text-muted-foreground">仅当时出现</div><div className="mt-1 text-xl font-semibold tabular-nums">{data.transitions.unmatched_realtime}</div></div>
              <div className="rounded-lg bg-muted/55 p-3"><div className="text-[11px] text-muted-foreground">仅事后出现</div><div className="mt-1 text-xl font-semibold tabular-nums">{data.transitions.unmatched_final}</div></div>
            </div>
            {enoughTransitions ? (
              <div className="rounded-xl border p-4">
                <div className="text-xs text-muted-foreground">确认滞后中位数</div>
                <div className="mt-1 text-2xl font-semibold">{signedMonth(data.transitions.lag_median_months)}</div>
                <div className="mt-1 text-xs text-muted-foreground">中间50%区间：{signedMonth(data.transitions.lag_q1_months)} 至 {signedMonth(data.transitions.lag_q3_months)}</div>
              </div>
            ) : (
              <div className="rounded-xl border border-dashed p-4 text-xs leading-5 text-muted-foreground">
                <strong className="block font-medium text-foreground">确认滞后：暂不可评估</strong>
                <span className="mt-1 block">匹配到的阶段切换少于{data.transitions.minimum_lag_sample}次，不展示中位滞后或区间，避免少量案例制造伪精度。</span>
              </div>
            )}
            {data.transitions.events.length > 0 && (
              <div className="space-y-2">
                {data.transitions.events.slice(-6).reverse().map((event) => (
                  <div key={`${event.final_phase}-${event.final_confirmation_period}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs">
                    <span>{phaseName(event.final_phase)} · 事后确认 {event.final_confirmation_period}</span>
                    <span className="text-muted-foreground">
                      {event.match_status === "matched" ? `当时 ${event.realtime_confirmation_period} · ${signedMonth(event.signed_lag_months)}` : "未匹配到当时确认"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <GitCompareArrows className="size-5" aria-hidden="true" />
              <CardTitle>疫情年份敏感性</CardTitle>
            </div>
            <CardDescription>对比全样本与剔除2020年；这里沿用原全标签口径，包含历史状态沿用。</CardDescription>
          </CardHeader>
          <CardContent>
            {robustnessReady ? (
              <div className="space-y-3">
                <div className="grid grid-cols-[1fr_auto_auto] gap-3 rounded-lg bg-muted/55 px-3 py-2 text-xs"><span>样本</span><span>可比月</span><span>含沿用标签一致</span></div>
                <div className="grid grid-cols-[1fr_auto_auto] gap-3 px-3 text-sm"><span>完整样本</span><span className="tabular-nums">{fullRobustness.comparable_months}</span><strong className="tabular-nums">{percent(fullRobustness.agreement_rate)}</strong></div>
                <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-t px-3 pt-3 text-sm"><span>剔除2020年</span><span className="tabular-nums">{exCovidRobustness.comparable_months}</span><strong className="tabular-nums">{percent(exCovidRobustness.agreement_rate)}</strong></div>
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-xl border border-dashed p-4 text-xs leading-5 text-muted-foreground">
                <CircleDashed className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                两个区间尚未同时达到{fullRobustness.minimum_rate_sample}个可比月，暂不比较疫情年份是否改变结论。
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Info className="size-5" aria-hidden="true" />
            <CardTitle>方法与边界</CardTitle>
          </div>
          <CardDescription>这是一项数据修订稳定性检验，不是预测未来的胜率统计。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">观察与决策时间</div><p className="mt-1 leading-5 text-muted-foreground">观察月的判断时点固定为次月20日18:00（北京时间），且只允许使用观察月末以前的数据。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">严格可见性</div><p className="mt-1 leading-5 text-muted-foreground">只有发布时间明确且不晚于判断时点的版本才会进入；发布时间未知的数据不会用抓取时间替代。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">历史版本选择</div><p className="mt-1 leading-5 text-muted-foreground">同一观察期采用当时已经公开的最新安全版本；无法定位数值或公式修订时间时，整组隔离而不猜测。</p></div>
          <div className="rounded-lg bg-muted/55 p-3"><div className="font-medium">事后参考</div><p className="mt-1 leading-5 text-muted-foreground">用今天数据库的最新值重算同一观察月，只衡量标签对数据修订的稳定性，不作为真实周期答案。</p></div>
        </CardContent>
      </Card>

      <div className="space-y-3 rounded-xl border border-dashed p-5 text-xs leading-6 text-muted-foreground">
        <div className="flex items-center gap-2 font-medium text-foreground">
          {activeStatus === "ok" ? <CheckCircle2 className="size-4" aria-hidden="true" /> : <TriangleAlert className="size-4" aria-hidden="true" />}
          必须带走的边界
        </div>
        <p>主动判断一致率只统计两边当月都可判且已有确认阶段的月份；含沿用标签一致率还会纳入历史状态沿用。两者都是数据修订稳定性，不是预测准确率。</p>
        <p>事后参考不是GDP真值或官方周期真值；本页也不评估模型预测未来的能力。</p>
        <p>{data.methodology_note}</p>
        <p>回测方法 v{data.backtest_definition.a3_methodology_version} · 最终快照生成于 {decisionTime(data.backtest_definition.final_cutoff_at)}（北京时间）</p>
        {data.warnings.map((warning) => <p key={warning}>• {warning}</p>)}
      </div>
    </div>
  );
}
