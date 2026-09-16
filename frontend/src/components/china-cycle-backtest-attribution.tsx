"use client";

import { useState, type SyntheticEvent } from "react";
import {
  ArrowDown,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  GitCompareArrows,
  Info,
  TriangleAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type {
  BusinessCycleBacktestAttribution,
  BusinessCycleBacktestAttributionMetric,
  BusinessCycleBacktestAttributionPhaseStep,
  BusinessCycleBacktestAttributionStepValue,
  BusinessCycleBacktestRegimeSnapshot,
  BusinessCyclePhase,
} from "@/lib/api";
import { getChinaBusinessCycleBacktestAttribution } from "@/lib/api";

const PHASE_LABELS: Record<BusinessCyclePhase, string> = {
  recovery: "复苏",
  expansion: "扩张",
  slowdown: "放缓",
  contraction: "收缩",
};

const METRIC_DEFINITIONS = [
  { key: "level_gap", label: "同步水平差", note: "决定经济相对自身历史中性线的位置" },
  { key: "momentum_3m", label: "同步动能", note: "决定近期动能改善或走弱" },
  { key: "leading_gap", label: "领先水平差", note: "观察领先信号相对历史中性线的位置" },
  { key: "leading_momentum_3m", label: "领先动能", note: "观察领先信号改善或走弱" },
] as const;

const ATTRIBUTION_REASON_LABELS: Record<string, string> = {
  realtime_support_insufficient: "当时可见信息不足，无法形成 R 路径",
  final_support_unavailable: "同端点最终信息不足，无法形成 F 路径",
  endpoint_phase_not_comparable: "R 与 F 未同时形成可比较阶段",
  realtime_support_not_exactly_matchable: "当时可见输入无法逐项匹配同键最终值",
  hybrid_endpoint_unavailable: "同一实时支撑集合无法形成 H 路径",
  coordinate_additivity_failed: "连续坐标的加总校验未通过",
};

type StepState = "changed" | "unchanged" | "not_comparable" | "unknown";

function phaseName(phase: BusinessCyclePhase | null | undefined): string {
  return phase ? PHASE_LABELS[phase] : "不可判断";
}

function snapshotBasis(snapshot: BusinessCycleBacktestRegimeSnapshot | null | undefined): string {
  if (!snapshot) return "本步不可比较";
  if (snapshot.phase_basis === "active_decision") {
    return snapshot.phase_status === "transition" ? "本月可判 · 切换观察" : "本月可判";
  }
  if (snapshot.phase_basis === "carried_forward") return "沿用上次确认";
  if (snapshot.phase_basis === "pending_confirmation") return "候选尚待确认";
  return "本月不可判";
}

function normalizeStep(value: BusinessCycleBacktestAttributionStepValue | undefined): StepState {
  if (value === true || value === "changed") return "changed";
  if (value === false || value === "unchanged") return "unchanged";
  if (value === "not_comparable") return "not_comparable";
  return "unknown";
}

function stepCopy(value: BusinessCycleBacktestAttributionStepValue | undefined): string {
  const state = normalizeStep(value);
  if (state === "changed") return "标签改变";
  if (state === "unchanged") return "标签未变";
  if (state === "not_comparable") return "不可比较";
  return "暂未提供";
}

function formatValue(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "--";
}

function formatDelta(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  if (Math.abs(value) < 0.005) return "0.00";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function attributionReason(reason: string): string {
  return ATTRIBUTION_REASON_LABELS[reason] ?? reason.replaceAll("_", " ");
}

function CounterfactualConnector({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 text-center text-[10px] leading-4 text-muted-foreground">
      <ArrowDown className="size-4 sm:hidden" aria-hidden="true" />
      <ArrowRight className="hidden size-4 sm:block" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function CounterfactualStage({
  code,
  title,
  note,
  snapshot,
}: {
  code: "R" | "H" | "F";
  title: string;
  note: string;
  snapshot?: BusinessCycleBacktestRegimeSnapshot | null;
}) {
  const displayPhase = snapshot?.confirmed_phase ?? snapshot?.phase;
  return (
    <div className="min-w-0 rounded-xl border bg-background/80 p-3">
      <div className="flex items-center gap-2">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-full border bg-muted text-[11px] font-semibold">
          {code}
        </span>
        <div className="min-w-0 text-xs font-semibold">{title}</div>
      </div>
      <p className="mt-1.5 text-[10px] leading-4 text-muted-foreground">{note}</p>
      {snapshot !== undefined && (
        <div className="mt-2 border-t pt-2">
          <div className="text-sm font-semibold">{snapshot ? phaseName(displayPhase) : "不可比较"}</div>
          <div className="mt-0.5 text-[10px] leading-4 text-muted-foreground">{snapshotBasis(snapshot)}</div>
        </div>
      )}
    </div>
  );
}

export function ChinaCycleBacktestAttributionLegend() {
  return (
    <div className="mb-4 rounded-xl border bg-muted/25 p-3 sm:p-4">
      <div className="flex items-center gap-2 text-xs font-semibold">
        <GitCompareArrows className="size-4" aria-hidden="true" />
        R → H → F：模型输出差异的反事实拆解
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_5.5rem_minmax(0,1fr)_6.5rem_minmax(0,1fr)] sm:items-stretch">
        <CounterfactualStage code="R" title="当时可见信息" note="只使用决策时点前已发布且可安全还原的数据。" />
        <CounterfactualConnector label="同键数值修订" />
        <CounterfactualStage code="H" title="已见数据换最终值" note="保留当时可得的输入支撑集合，不加入当时看不到的新输入。" />
        <CounterfactualConnector label="新增输入及相互作用" />
        <CounterfactualStage code="F" title="完整事后参考" note="用今天的完整最终信息，在同一观察端点重算。" />
      </div>
      <div className="mt-3 flex items-start gap-2 rounded-lg border border-dashed bg-background/60 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <p>
          这是模型内部的反事实审计，只回答沿 R→H→F 的预设替换顺序，模型输出怎样变化；不说明某项数据造成了现实经济变化。拆解具有路径依赖，两类变化可以并存，不能据此分配单一原因占比。
        </p>
      </div>
    </div>
  );
}

function PhaseStepCard({
  title,
  step,
}: {
  title: string;
  step: BusinessCycleBacktestAttributionPhaseStep | null | undefined;
}) {
  if (!step) return null;
  return (
    <div className="rounded-lg border bg-background/70 p-3">
      <div className="text-xs font-semibold">{title}</div>
      <div className="mt-2 space-y-1.5 text-[11px]">
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">R → H 数值修订</span>
          <span className="font-medium">{stepCopy(step.revision_step)}</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">H → F 新增输入等</span>
          <span className="font-medium">{stepCopy(step.support_step)}</span>
        </div>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  note,
  metric,
}: {
  label: string;
  note: string;
  metric: BusinessCycleBacktestAttributionMetric;
}) {
  const metricUnavailable = metric.status != null && metric.status !== "available";
  const valuesComplete = [metric.realtime, metric.hybrid, metric.final].every(
    (value) => typeof value === "number" && Number.isFinite(value),
  );
  const deltasComplete = [
    metric.revision_path_delta,
    metric.support_expansion_path_delta,
    metric.total_delta,
  ].every((value) => typeof value === "number" && Number.isFinite(value));
  const auditPassed = metric.additivity_passed === true;

  if (
    metric.status === "additivity_failed"
    || (metric.status === "available" && metric.additivity_passed === false)
  ) {
    return (
      <div className="rounded-xl border border-dashed p-3">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <TriangleAlert className="size-3.5" aria-hidden="true" />
          {label}
        </div>
        <p className="mt-1 text-[11px] leading-5 text-muted-foreground">加总校验未通过，拆解数值已隐藏。</p>
      </div>
    );
  }

  if (metricUnavailable || !valuesComplete || !deltasComplete || !auditPassed) {
    return (
      <div className="rounded-xl border border-dashed p-3">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <CircleDashed className="size-3.5" aria-hidden="true" />
          {label}
        </div>
        <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
          三端连续量或加总校验尚不完整，本项不展示反事实数值。
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-background/75 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold">{label}</div>
          <div className="mt-0.5 text-[10px] leading-4 text-muted-foreground">{note}</div>
        </div>
        <Badge variant="outline" className="gap-1 text-[10px] font-normal">
          <CheckCircle2 className="size-3" aria-hidden="true" />
          加总通过
        </Badge>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        {([
          ["R 当时", metric.realtime],
          ["H 同支撑", metric.hybrid],
          ["F 完整", metric.final],
        ] as const).map(([name, value]) => (
          <div key={name} className="rounded-lg bg-muted/55 px-2 py-2">
            <div className="text-[10px] text-muted-foreground">{name}</div>
            <div className="mt-0.5 text-sm font-semibold tabular-nums">{formatValue(value)}</div>
          </div>
        ))}
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg border border-dashed px-2.5 py-2">
          <div className="text-[10px] leading-4 text-muted-foreground">数值修订差 R−H</div>
          <div className="mt-0.5 text-sm font-semibold tabular-nums">{formatDelta(metric.revision_path_delta)}</div>
        </div>
        <div className="rounded-lg border border-dashed px-2.5 py-2">
          <div className="text-[10px] leading-4 text-muted-foreground">新增输入及相互作用 H−F</div>
          <div className="mt-0.5 text-sm font-semibold tabular-nums">{formatDelta(metric.support_expansion_path_delta)}</div>
        </div>
        <div className="rounded-lg border border-dashed px-2.5 py-2">
          <div className="text-[10px] leading-4 text-muted-foreground">总差 R−F</div>
          <div className="mt-0.5 text-sm font-semibold tabular-nums">{formatDelta(metric.total_delta)}</div>
        </div>
      </div>
      <div className="mt-2 text-[10px] leading-4 text-muted-foreground">
        校验：R−F = (R−H) + (H−F){typeof metric.residual === "number" ? ` · 残差 ${formatDelta(metric.residual)}` : ""}
      </div>
    </div>
  );
}

function SupportAudit({ attribution }: { attribution: BusinessCycleBacktestAttribution }) {
  const audit = attribution.support_audit;
  if (!audit) return null;

  const missingKeys = audit.missing_counterpart_keys ?? [];
  const missingFiscalPeriods = missingKeys
    .filter((key) => key.startsWith("CN_FISCAL_IMPULSE_PROXY:"))
    .map((key) => key.split(":")[1]?.slice(0, 7))
    .filter((period): period is string => Boolean(period));

  const counts = [
    audit.realtime_support_count != null ? `R 历史观测 ${audit.realtime_support_count} 条` : null,
    audit.hybrid_support_count != null ? `H 同键观测 ${audit.hybrid_support_count} 条` : null,
    audit.final_support_count != null ? `F 历史观测 ${audit.final_support_count} 条` : null,
    audit.matched_final_value_count != null ? `匹配同键最终值 ${audit.matched_final_value_count} 条` : null,
    audit.revised_input_count != null ? `发生数值修订 ${audit.revised_input_count} 条` : null,
    audit.expanded_input_count != null ? `新增可用观测 ${audit.expanded_input_count} 条` : null,
    audit.missing_counterpart_count ? `缺少同键最终值 ${audit.missing_counterpart_count} 条` : null,
    audit.ambiguous_counterpart_count ? `同键匹配不唯一 ${audit.ambiguous_counterpart_count} 条` : null,
    audit.formula_mismatch_count ? `公式口径不匹配 ${audit.formula_mismatch_count} 条` : null,
  ].filter((item): item is string => item !== null);
  const flags = [
    audit.basis_changed_on_revision_path ? "R→H 共同篮子发生变化" : null,
    audit.basis_changed_on_support_path ? "H→F 共同篮子发生变化" : null,
    audit.decision_eligibility_changed_on_revision_path ? "R→H 可决策条件发生变化" : null,
    audit.decision_eligibility_changed_on_support_path ? "H→F 可决策条件发生变化" : null,
    audit.state_path_changed ? "端点候选或确认状态不同" : null,
  ].filter((item): item is string => item !== null);

  if (counts.length === 0 && flags.length === 0) return null;
  return (
    <div className="rounded-xl border border-dashed bg-background/60 p-3">
      <div className="text-xs font-semibold">支撑集合与状态机审计</div>
      {counts.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {counts.map((item) => <Badge key={item} variant="outline" className="font-normal">{item}</Badge>)}
        </div>
      )}
      {flags.length > 0 && (
        <div className="mt-2 space-y-1 text-[11px] leading-5 text-muted-foreground">
          {flags.map((item) => <div key={item}>• {item}</div>)}
        </div>
      )}
      {missingFiscalPeriods.length > 0 && (
        <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
          当前缺口来自财政脉冲代理：{missingFiscalPeriods.join("、")}；首发证据没有同键最终值，系统不会假定它从未修订。
        </p>
      )}
    </div>
  );
}

function attributionSummary(attribution: BusinessCycleBacktestAttribution): string {
  const steps = [
    attribution.phase_steps?.display,
    attribution.phase_steps?.confirmed,
    attribution.phase_steps?.raw,
  ];
  const revisionChanged = steps.some(
    (step) => normalizeStep(step?.revision_step) === "changed",
  );
  const supportChanged = steps.some(
    (step) => normalizeStep(step?.support_step) === "changed",
  );

  if (revisionChanged && supportChanged) {
    return "两段替换都改变了至少一层模型标签，最终差异不能归为单一来源。";
  }
  if (revisionChanged) {
    return "差异在 R→H 这一步已经出现：只替换当时已见数据的最终值，模型标签就发生了变化。";
  }
  if (supportChanged) {
    return "R→H 没有改变可比较标签；加入当时不可得输入及其相互作用后，模型标签发生了变化。";
  }
  return "阶段标签在两步替换中没有发生可比较变化；下方连续坐标仍记录模型数值如何移动。";
}

function AttributionUnavailable({ attribution }: { attribution: BusinessCycleBacktestAttribution }) {
  const copy = attribution.status === "hybrid_unavailable"
    ? "当时可见的输入支撑集合无法完整匹配最终值，不能安全构造 H 路径。"
    : attribution.status === "additivity_failed"
      ? "连续坐标的加总校验没有通过，拆解结果已隐藏。"
      : "R、H、F 三端没有形成完整可比结果，本月不作反事实拆解。";
  return (
    <div className="flex items-start gap-2 rounded-xl border border-dashed bg-muted/30 p-4 text-xs leading-5 text-muted-foreground">
      <CircleDashed className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div>
        <div className="font-semibold text-foreground">本月暂不可拆解</div>
        <p className="mt-1">{copy}</p>
        {(attribution.reasons?.length ?? 0) > 0 && (
          <p className="mt-1">审计说明：{attribution.reasons?.map(attributionReason).join("；")}</p>
        )}
      </div>
    </div>
  );
}

function AttributionContent({
  attribution,
  fallbackRealtime,
  fallbackFinal,
}: {
  attribution: BusinessCycleBacktestAttribution;
  fallbackRealtime: BusinessCycleBacktestRegimeSnapshot | null;
  fallbackFinal: BusinessCycleBacktestRegimeSnapshot | null;
}) {
  if (attribution.status !== "available") {
    return (
      <div className="space-y-3 rounded-xl bg-muted/30 p-3 sm:p-4">
        <AttributionUnavailable attribution={attribution} />
        <SupportAudit attribution={attribution} />
        {(attribution.warnings?.length ?? 0) > 0 && (
          <div className="space-y-1 text-[11px] leading-5 text-muted-foreground">
            {attribution.warnings?.map((warning) => <div key={warning}>• {warning}</div>)}
          </div>
        )}
      </div>
    );
  }

  const metrics = METRIC_DEFINITIONS.flatMap((definition) => {
    const metric = attribution.metrics?.[definition.key];
    return metric ? [{ ...definition, metric }] : [];
  });
  const realtime = attribution.realtime ?? fallbackRealtime;
  const final = attribution.final ?? fallbackFinal;

  return (
    <div className="space-y-3 rounded-xl bg-muted/30 p-3 sm:p-4">
      <div className="text-xs leading-5">
        <span className="font-semibold">本月怎么读：</span>
        <span className="text-muted-foreground">{attributionSummary(attribution)}</span>
      </div>

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_5.5rem_minmax(0,1fr)_6.5rem_minmax(0,1fr)] sm:items-stretch">
        <CounterfactualStage code="R" title="当时可见信息" note="决策时点的严格历史现场" snapshot={realtime} />
        <CounterfactualConnector label="同键数值修订" />
        <CounterfactualStage code="H" title="已见数据换最终值" note="不加入当时看不到的新输入" snapshot={attribution.hybrid} />
        <CounterfactualConnector label="新增输入及相互作用" />
        <CounterfactualStage code="F" title="完整事后参考" note="同端点的完整最终信息" snapshot={final} />
      </div>

      {attribution.phase_steps && (
        <div>
          <div className="mb-2 text-xs font-semibold">标签在哪一步改变</div>
          <div className="grid gap-2 sm:grid-cols-3">
            <PhaseStepCard title="页面展示阶段" step={attribution.phase_steps.display} />
            <PhaseStepCard title="已确认阶段" step={attribution.phase_steps.confirmed} />
            <PhaseStepCard title="规则象限（含死区继承）" step={attribution.phase_steps.raw} />
          </div>
        </div>
      )}

      <SupportAudit attribution={attribution} />

      {metrics.length > 0 && (
        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-semibold">连续坐标拆解</div>
            <div className="text-[10px] text-muted-foreground">正负只表示坐标差，不代表利好或利空</div>
          </div>
          <div className="grid gap-2 lg:grid-cols-2">
            {metrics.map(({ key, label, note, metric }) => (
              <MetricCard key={key} label={label} note={note} metric={metric} />
            ))}
          </div>
        </div>
      )}

      {(attribution.warnings?.length ?? 0) > 0 && (
        <div className="space-y-1 text-[11px] leading-5 text-muted-foreground">
          {attribution.warnings?.map((warning) => <div key={warning}>• {warning}</div>)}
        </div>
      )}

      {attribution.a1_methodology_version && attribution.a2_methodology_version
        && attribution.a3_methodology_version && attribution.attribution_methodology_version && (
        <div className="text-[10px] leading-4 text-muted-foreground">
          方法链：A1 v{attribution.a1_methodology_version} · A2 v{attribution.a2_methodology_version}
          {" · "}A3 v{attribution.a3_methodology_version} · A5c v{attribution.attribution_methodology_version}
        </div>
      )}

      <div className="flex items-start gap-2 rounded-lg border border-dashed bg-background/65 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <p>这里只解释模型输出在预设替换顺序下如何变化，不把数据修订、输入可得性或任何指标表述为现实经济变化的因果。</p>
      </div>
    </div>
  );
}

export function ChinaCycleBacktestAttributionDetail({
  period,
  realtime,
  final,
}: {
  period: string;
  realtime: BusinessCycleBacktestRegimeSnapshot | null;
  final: BusinessCycleBacktestRegimeSnapshot | null;
}) {
  const [attribution, setAttribution] = useState<BusinessCycleBacktestAttribution | null>(null);
  const [loading, setLoading] = useState(false);
  const [requested, setRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadAttribution() {
    setLoading(true);
    setError(null);
    try {
      const result = await getChinaBusinessCycleBacktestAttribution(period);
      setAttribution(result);
    } catch {
      setError("暂时无法取得本月拆解，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }

  function handleToggle(event: SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || requested) return;
    setRequested(true);
    void loadAttribution();
  }

  function retry() {
    void loadAttribution();
  }

  return (
    <details className="group mt-3 border-t pt-2" onToggle={handleToggle}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-2 py-2 text-xs font-semibold hover:bg-muted/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2">
          <GitCompareArrows className="size-4 shrink-0" aria-hidden="true" />
          查看模型差异拆解
          <Badge variant="outline" className="hidden font-normal sm:inline-flex">按月计算 · 反事实审计</Badge>
        </span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>

      <div className="mt-2">
        {loading && (
          <div className="flex min-h-24 items-center justify-center gap-2 rounded-xl border border-dashed bg-muted/25 p-4 text-xs text-muted-foreground" role="status">
            <CircleDashed className="size-4 animate-spin" aria-hidden="true" />
            正在按 {period} 的同一观察端点重算 R、H、F 三条路径…
          </div>
        )}
        {!loading && error && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-4 text-xs">
            <div className="flex items-start gap-2 text-muted-foreground">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
            <button
              type="button"
              onClick={retry}
              className="min-h-9 rounded-md border px-3 font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              重试
            </button>
          </div>
        )}
        {!loading && !error && attribution && (
          <AttributionContent
            attribution={attribution}
            fallbackRealtime={realtime}
            fallbackFinal={final}
          />
        )}
      </div>
    </details>
  );
}
