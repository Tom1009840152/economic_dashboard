import type {
  BusinessCyclePhase,
  BusinessCycleRegimePoint,
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

export interface BusinessCycleHeadline {
  title: string;
  secondary: string;
  timing: string[];
}

export function regimeHeadline(
  point: BusinessCycleRegimePoint,
  fallbackLastDecisionPeriod: string | null = null,
): BusinessCycleHeadline {
  const confirmedPhase = point.confirmed_phase ?? point.phase;
  const confirmedLabel = confirmedPhase ? PHASE_NAMES[confirmedPhase] : null;
  const lastDecisionPeriod = point.last_decision_period ?? fallbackLastDecisionPeriod;

  if (point.phase_basis === "carried_forward") {
    return {
      title: "本月暂不可判",
      secondary: confirmedLabel ? `上次确认：${confirmedLabel}` : "尚无已确认阶段",
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
  const prefix = point.phase_basis === "carried_forward"
    ? "上次确认阶段的含义："
    : point.phase_basis === "pending_confirmation"
      ? "当前候选方向："
      : point.phase_status === "transition"
        ? "当前有效阶段："
        : "";
  return `${prefix}${phasePlainLabel(point.phase)}`;
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
  return earlier.findLast((month) => month.decision_eligible && month.phase !== null) ?? null;
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
  const positive = point.positive_contributions[0]?.name;
  const negative = point.negative_contributions[0]?.name;
  const parts = [
    positive ? `较大的模型内正贡献来自${positive}` : null,
    negative ? `较大的模型内负贡献来自${negative}` : null,
  ].filter((item): item is string => item !== null);

  if (parts.length === 0) {
    return "本月暂无可展示的同步指数贡献分解。贡献只解释指数构成，不代表现实因果。";
  }
  return `本期同步指数相对 100 的构成中，${parts.join("；")}。这只解释模型内的当期指数构成，不代表现实因果，也不是三月动能的分解。`;
}
