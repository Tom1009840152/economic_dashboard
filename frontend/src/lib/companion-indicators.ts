// 有些指标同时看"同比增速"和"绝对水平"更完整（比如出口：同比看动能、绝对值看规模）。
// 这里配对的是"绝对水平"那个指标的代码，只作为主指标详情页里的第二个面板出现，
// 不会单独出现在国家页的卡片网格里。
export const ABSOLUTE_COMPANION: Record<string, string> = {
  CN_EXPORTS: "CN_EXPORTS_ABS",
};

export const COMPANION_CODES = new Set(Object.values(ABSOLUTE_COMPANION));
