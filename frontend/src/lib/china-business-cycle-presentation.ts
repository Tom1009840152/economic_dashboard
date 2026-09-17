import type {
  BusinessCycleDriverDecomposition,
  BusinessCyclePhase,
  BusinessCycleRegimePoint,
  BusinessCycleStateChangeReason,
} from "@/lib/api";

const PHASE_NAMES: Record<BusinessCyclePhase, string> = {
  recovery: "复苏",
  expansion: "扩张",
  slowdown: "放缓",
  contraction: "收缩",
};

const PHASE_PLAIN_LABELS: Record<BusinessCyclePhase, string> = {
  recovery: "偏弱，但正在改善",
  expansion: "偏强，且仍在改善",
  slowdown: "偏强，但正在走弱",
  contraction: "偏弱，且仍在走弱",
};

const LEVEL_AXIS_LABELS: Record<BusinessCycleRegimePoint["level_axis"], string> = {
  above: "活动水平偏强",
  below: "活动水平偏弱",
  neutral: "活动水平接近历史中性",
  unavailable: "活动水平暂不可比",
};

const MOMENTUM_AXIS_LABELS: Record<BusinessCycleRegimePoint["momentum_axis"], string> = {
  rising: "近三个月动能改善",
  falling: "近三个月动能走弱",
  neutral: "近三个月动能接近持平",
  unavailable: "近三个月动能暂不可比",
};

export function phasePlainLabel(phase: BusinessCyclePhase | null): string {
  return phase ? PHASE_PLAIN_LABELS[phase] : "强弱和方向暂时无法判断";
}

export function currentPhaseForDisplay(point: BusinessCycleRegimePoint): BusinessCyclePhase | null {
  if (point.current_phase !== undefined) return point.current_phase;
  if (point.phase_status === "stale") return null;
  if (point.phase_basis === "active_decision" || point.phase_status === "held_uncomparable") {
    return point.confirmed_phase;
  }
  return null;
}

export interface BusinessCycleHeadline {
  title: string;
  secondary: string;
  timing: string[];
}

export function regimeHeadline(
  point: BusinessCycleRegimePoint,
  fallbackLastDecisionPeriod: string | null = null,
): BusinessCycleHeadline {
  const currentPhase = currentPhaseForDisplay(point);
  const confirmedPhase = currentPhase ?? point.confirmed_phase ?? point.phase;
  const confirmedLabel = confirmedPhase ? PHASE_NAMES[confirmedPhase] : null;
  const lastDecisionPeriod = point.last_decision_period ?? fallbackLastDecisionPeriod;

  if (point.phase_basis === "carried_forward") {
    const expired = currentPhase === null;
    return {
      title: "本月暂不可判",
      secondary: confirmedLabel
        ? `${expired ? "最近确认" : "暂时沿用"}：${confirmedLabel}${expired ? "（仅作历史参考）" : ""}`
        : "尚无已确认阶段",
      timing: [
        point.confirmed_since ? `确认于 ${point.confirmed_since}` : null,
        lastDecisionPeriod ? `最近可判 ${lastDecisionPeriod}` : "暂无可判定月",
        `已连续 ${point.carry_forward_months} 个月未更新`,
      ].filter((item): item is string => item !== null),
    };
  }

  if (point.phase_basis === "active_decision") {
    return {
      title: point.phase_status === "transition"
        ? `当前有效阶段：${confirmedLabel ?? "待确认"}`
        : `当前判断：${confirmedLabel ?? "待确认"}`,
      secondary: point.phase_status === "transition"
        ? "本月可判，阶段切换仍在观察"
        : "本月满足判定条件",
      timing: [
        point.confirmed_since ? `确认于 ${point.confirmed_since}` : null,
        lastDecisionPeriod ? `最近可判 ${lastDecisionPeriod}` : null,
      ].filter((item): item is string => item !== null),
    };
  }

  if (point.phase_basis === "pending_confirmation") {
    const candidateLabel = point.candidate_phase
      ? PHASE_NAMES[point.candidate_phase]
      : point.phase
        ? PHASE_NAMES[point.phase]
        : null;
    const progress = point.required_confirmation_months == null
      ? `${point.candidate_streak} 个月`
      : `${point.candidate_streak}/${point.required_confirmation_months} 个月`;
    return {
      title: "本月暂不可判",
      secondary: candidateLabel
        ? `候选方向：${candidateLabel}（尚待连续确认）`
        : "尚无已确认阶段",
      timing: [
        "尚无已确认阶段",
        candidateLabel ? `候选已连续 ${progress}` : null,
        lastDecisionPeriod ? `最近形成判断条件 ${lastDecisionPeriod}` : null,
      ].filter((item): item is string => item !== null),
    };
  }

  return {
    title: "本月暂不可判",
    secondary: point.confirmed_phase
      ? `上次确认：${PHASE_NAMES[point.confirmed_phase]}`
      : "尚无已确认阶段",
    timing: [
      point.confirmed_since ? `确认于 ${point.confirmed_since}` : null,
      lastDecisionPeriod ? `最近可判 ${lastDecisionPeriod}` : "暂无可判定月",
    ].filter((item): item is string => item !== null),
  };
}

export function phasePlainHeadline(point: BusinessCycleRegimePoint): string {
  const currentPhase = currentPhaseForDisplay(point);
  if (point.phase_basis === "pending_confirmation") {
    return `当前候选方向：${phasePlainLabel(point.candidate_phase ?? point.phase)}`;
  }
  if (currentPhase === null) {
    return "当前状态：强弱和方向暂时无法判断";
  }
  const prefix = point.phase_basis === "carried_forward"
    ? "暂时沿用阶段的含义："
    : point.phase_status === "transition"
      ? "当前有效阶段："
      : "";
  return `${prefix}${phasePlainLabel(currentPhase)}`;
}

export function phaseAxisSummary(point: BusinessCycleRegimePoint): string {
  if (!point.decision_eligible) {
    return point.confirmed_phase
      ? "本月未满足口径可比条件，以上是最近一次确认阶段；当前坐标只作诊断，不用于阶段切换。"
      : "本月未满足口径可比条件，模型尚无已确认阶段；当前坐标只作诊断，不用于阶段切换。";
  }
  const axes = `${LEVEL_AXIS_LABELS[point.level_axis]}，${MOMENTUM_AXIS_LABELS[point.momentum_axis]}`;
  if (point.candidate_phase) {
    return `${axes}；当前坐标形成${PHASE_NAMES[point.candidate_phase]}候选，已累计 ${point.candidate_streak} 个可比月，但尚未完成连续确认。`;
  }
  return `${axes}。`;
}

export function absoluteAnchorQualifier(point: BusinessCycleRegimePoint): string {
  if (point.absolute_anchor.state === "contractionary") {
    return "多数 PMI 类绝对锚仍在收缩区，不能把相对改善理解成经济已全面转强。";
  }
  if (point.absolute_anchor.state === "expansionary") {
    return "多数 PMI 类绝对锚处于扩张区，绝对景气与相对方向较为一致。";
  }
  if (point.absolute_anchor.state === "mixed") {
    return "PMI 类绝对锚仍有分化，行业强弱并不一致。";
  }
  return "绝对荣枯锚数据不足，暂不据此判断经济是否全面扩张。";
}

export function previousComparableMonth(
  months: BusinessCycleRegimePoint[],
  current: BusinessCycleRegimePoint,
): BusinessCycleRegimePoint | null {
  const earlier = months.filter((month) => month.period < current.period);
  const backendPeriod = current.state_change.previous_decision_period;
  if (backendPeriod) {
    const backendMonth = earlier.find((month) => month.period === backendPeriod);
    if (backendMonth) {
      return backendMonth;
    }
  }
  return earlier.findLast((month) => month.decision_eligible && month.phase !== null) ?? null;
}

const STATE_CHANGE_LABELS: Record<BusinessCycleStateChangeReason, string> = {
  first_decision: "这是首个满足条件的判断月",
  level_axis_changed: "活动水平轴发生变化",
  momentum_axis_changed: "三月动能方向发生变化",
  raw_phase_changed: "四象限位置发生变化",
  candidate_started: "形成了新的阶段候选",
  candidate_progressed: "候选阶段又积累了一个连续月",
  candidate_reset: "此前未确认候选中断并重置",
  candidate_cleared: "此前候选已经消失",
  phase_confirmed: "连续确认门槛已经满足",
  phase_maintained: "当前坐标仍支持原有状态",
  phase_carried_forward: "本月只沿用上次确认，不形成新判断",
  basis_changed_hold: "共同指标篮子变化，本月暂停判定",
  insufficient_hold: "共同成分或连续历史不足，本月暂停判定",
  dead_zone_unclassified: "数据完整，但至少一个轴仍在判定死区，暂不能归入四象限",
  level_dead_zone_inherited: "活动水平位于死区，沿用已确认方向",
  momentum_dead_zone_inherited: "三月动能位于死区，沿用已确认方向",
  leading_shortened_confirmation: "领先方向同向，把确认要求缩短为连续两个月",
};

export function stateChangeExplanation(point: BusinessCycleRegimePoint): string {
  const reasons = point.state_change.reason_codes.map((reason) => STATE_CHANGE_LABELS[reason]);
  if (reasons.length === 0) {
    return "本月没有可识别的状态机变化。";
  }
  const prefix = point.state_change.previous_decision_period
    ? `相对上一判断月 ${point.state_change.previous_decision_period}`
    : "从状态机起点看";
  return `${prefix}：${reasons.join("；")}。这些是模型规则的机械解释，不表示现实经济因果。`;
}

function strongestDriver(
  decomposition: BusinessCycleDriverDecomposition,
  field: "level_contribution" | "momentum_contribution",
): string | null {
  const item = [...decomposition.drivers]
    .sort((left, right) => Math.abs(right[field]) - Math.abs(left[field]))[0];
  return item ? `${item.name}（${item[field] > 0 ? "+" : ""}${item[field].toFixed(2)}点）` : null;
}

export function comparableMonthExplanation(
  current: BusinessCycleRegimePoint,
  previous: BusinessCycleRegimePoint | null,
): string {
  if (!current.decision_eligible) {
    if (!previous) {
      return "本月未满足可比条件，当前窗口内也没有更早的有效判断月，模型暂不更新阶段。";
    }
    const previousLabel = previous.phase ? PHASE_NAMES[previous.phase] : "暂未定位";
    return `本月未满足可比条件，模型没有据此更新阶段；最近可比月为 ${previous.period}，当时是${previousLabel}（${phasePlainLabel(previous.phase)}）。`;
  }

  if (!previous) {
    return `这是当前窗口内首个可比判断月：${phaseAxisSummary(current)}阶段标签还需服从连续确认规则。`;
  }

  const currentPhase = current.phase ? `${PHASE_NAMES[current.phase]}（${phasePlainLabel(current.phase)}）` : "暂未定位";
  const previousPhase = previous.phase ? `${PHASE_NAMES[previous.phase]}（${phasePlainLabel(previous.phase)}）` : "暂未定位";
  const phaseChange = current.phase !== previous.phase
    ? `模型标签由${previousPhase}变为${currentPhase}`
    : `模型标签仍为${currentPhase}`;
  return `与上一可比月 ${previous.period} 相比，${phaseChange}；${phaseAxisSummary(current)}本期判断由这两个坐标与连续确认规则共同形成。`;
}

export function contributionExplanation(point: BusinessCycleRegimePoint): string {
  const decomposition = point.coincident_decomposition;
  if (decomposition.status !== "available") {
    return "本月六个月共同篮子不足，无法对水平与动能做严格的逐项加总分解。";
  }
  const level = strongestDriver(decomposition, "level_contribution");
  const momentum = strongestDriver(decomposition, "momentum_contribution");
  const audit = decomposition.additivity_passed ? "两组贡献均已通过加总校验" : "加总校验未通过";
  return `三月水平相对中性的最大驱动是${level ?? "暂无"}；三月动能的最大驱动是${momentum ?? "暂无"}。${audit}。这是模型内分解，不表示经济因果。`;
}
