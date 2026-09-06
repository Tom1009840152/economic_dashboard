"use client";

import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { DataPoint } from "@/lib/api";

type RangeKey = "1M" | "3M" | "1Y" | "ALL";

const RANGES: { key: RangeKey; label: string; days: number | null }[] = [
  { key: "1M", label: "近 1 个月", days: 30 },
  { key: "3M", label: "近 3 个月", days: 90 },
  { key: "1Y", label: "近 1 年", days: 365 },
  { key: "ALL", label: "全部", days: null },
];

function percentChange(current: number, previous: number): number {
  return ((current / previous) - 1) * 100;
}

function signedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function changeClass(value: number): string {
  return value >= 0 ? "text-emerald-600" : "text-red-600";
}

export function CnyJpyTopic({
  jpyCnyHistory,
  usdCnyHistory,
}: {
  jpyCnyHistory: DataPoint[];
  usdCnyHistory: DataPoint[];
}) {
  const [range, setRange] = useState<RangeKey>("1Y");

  const rows = useMemo(() => {
    const usdCnyByDate = new Map(usdCnyHistory.map((point) => [point.date, point.value]));
    return jpyCnyHistory
      .map((point) => {
        const usdCny = usdCnyByDate.get(point.date);
        if (!usdCny || point.value <= 0) return null;
        const cnyJpy = 100 / point.value;
        return {
          date: point.date,
          cnyJpy,
          jpyCny100: point.value,
          usdCny,
          usdJpy: usdCny * cnyJpy,
        };
      })
      .filter((row): row is NonNullable<typeof row> => row !== null);
  }, [jpyCnyHistory, usdCnyHistory]);

  const displayRows = useMemo(() => {
    const days = RANGES.find((item) => item.key === range)?.days;
    if (!days || rows.length === 0) return rows;
    const lastDate = new Date(`${rows[rows.length - 1].date}T00:00:00`);
    const start = new Date(lastDate);
    start.setDate(start.getDate() - days);
    return rows.filter((row) => new Date(`${row.date}T00:00:00`) >= start);
  }, [range, rows]);

  const latest = displayRows.at(-1);
  const start = displayRows[0];
  const totalChange = latest && start ? percentChange(latest.cnyJpy, start.cnyJpy) : null;
  // CNY/JPY = USD/JPY ÷ USD/CNY. These two legs explain the direction of the cross rate.
  const yenSide = latest && start ? percentChange(latest.usdJpy, start.usdJpy) : null;
  const yuanSide = latest && start ? -percentChange(latest.usdCny, start.usdCny) : null;

  if (!latest || !start) {
    return <p className="text-sm text-muted-foreground">暂无足够的人民币与日元共同交易日数据。</p>;
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-[1.45fr_1fr_1fr]">
        <Card className="md:col-span-1">
          <CardHeader>
            <CardTitle>人民币兑日元（CNY/JPY）</CardTitle>
            <CardDescription>1 人民币可兑换的日元</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-4xl font-semibold tabular-nums">{latest.cnyJpy.toFixed(3)}</span>
              <span className="text-sm text-muted-foreground">日元</span>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              等价于 100 日元 = {latest.jpyCny100.toFixed(4)} 人民币
            </p>
            <p className={`mt-3 text-sm font-medium ${changeClass(totalChange ?? 0)}`}>
              {RANGES.find((item) => item.key === range)?.label} {signedPercent(totalChange ?? 0)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>日元侧</CardTitle>
            <CardDescription>隐含 USD/JPY 的变动</CardDescription>
          </CardHeader>
          <CardContent>
            <div className={`text-3xl font-semibold tabular-nums ${changeClass(yenSide ?? 0)}`}>
              {signedPercent(yenSide ?? 0)}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">上升代表日元相对美元走弱，推高 CNY/JPY。</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>人民币侧</CardTitle>
            <CardDescription>USD/CNY 的反向变动</CardDescription>
          </CardHeader>
          <CardContent>
            <div className={`text-3xl font-semibold tabular-nums ${changeClass(yuanSide ?? 0)}`}>
              {signedPercent(yuanSide ?? 0)}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">上升代表人民币相对美元走强，推高 CNY/JPY。</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>走势与变动拆解</CardTitle>
              <CardDescription>
                CNY/JPY = USD/JPY ÷ USD/CNY；拆解用于识别变动主要来自日元侧还是人民币侧，不代表因果判断。
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              {RANGES.map((item) => (
                <button
                  key={item.key}
                  onClick={() => setRange(item.key)}
                  className={`rounded-full border px-3 py-1 text-sm transition-colors ${
                    range === item.key
                      ? "border-foreground bg-foreground text-background"
                      : "border-border text-muted-foreground hover:border-foreground/40"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="h-[360px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={displayRows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={52} />
                <YAxis domain={["auto", "auto"]} tickLine={false} axisLine={false} width={54} />
                <Tooltip
                  formatter={(value) => [Number(value).toFixed(3), "1 人民币兑日元"]}
                  labelFormatter={(label) => `日期：${label}`}
                />
                <Line dataKey="cnyJpy" stroke="var(--foreground)" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>如何阅读</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p><span className="font-medium text-foreground">CNY/JPY 上升</span>：人民币对日元升值；同样的人民币可兑换更多日元。</p>
          <p><span className="font-medium text-foreground">日元侧为主</span>：重点留意日本货币政策、日美利差与全球避险情绪。</p>
          <p><span className="font-medium text-foreground">人民币侧为主</span>：重点留意中国增长预期、美元强弱与跨境资金环境。</p>
        </CardContent>
      </Card>
    </div>
  );
}
