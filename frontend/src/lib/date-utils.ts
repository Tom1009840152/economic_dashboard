export const GRANULARITY_OPTIONS = [
  { key: "day", label: "按天" },
  { key: "week", label: "按周" },
  { key: "month", label: "按月" },
  { key: "quarter", label: "按季度" },
  { key: "year", label: "按年" },
] as const;

export type Granularity = (typeof GRANULARITY_OPTIONS)[number]["key"];

export const RANGE_PRESETS = [
  { key: "1M", label: "近1个月", days: 30 },
  { key: "3M", label: "近3个月", days: 90 },
  { key: "6M", label: "近6个月", days: 180 },
  { key: "1Y", label: "近1年", days: 365 },
  { key: "3Y", label: "近3年", days: 365 * 3 },
  { key: "ALL", label: "全部", days: null },
] as const;

export function shiftDate(dateStr: string, days: number): string {
  const d = new Date(dateStr);
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function periodKey(dateStr: string, granularity: Granularity): string {
  const d = new Date(dateStr);
  const year = d.getFullYear();
  switch (granularity) {
    case "week": {
      const jan1 = new Date(year, 0, 1);
      const dayOfYear = Math.floor((d.getTime() - jan1.getTime()) / 86400000);
      const week = Math.ceil((dayOfYear + jan1.getDay() + 1) / 7);
      return `${year}-W${week}`;
    }
    case "month":
      return `${year}-${d.getMonth()}`;
    case "quarter":
      return `${year}-Q${Math.floor(d.getMonth() / 3)}`;
    case "year":
      return `${year}`;
    default:
      return dateStr;
  }
}

// 同一周期内保留最后一个数据点（期末值），符合金融/经济序列惯例；points 需要按日期升序排列
export function resample<T extends { date: string }>(points: T[], granularity: Granularity): T[] {
  if (granularity === "day") return points;
  const map = new Map<string, T>();
  for (const p of points) {
    map.set(periodKey(p.date, granularity), p);
  }
  return Array.from(map.values());
}
