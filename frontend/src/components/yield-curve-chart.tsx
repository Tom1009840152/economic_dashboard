"use client";

import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { DataPoint } from "@/lib/api";

// 主题本身是灰度配色，多条线叠在一起时区分不出来，这里对多序列对比图单独用几个
// 色相区分度明显的颜色，不走 --chart-N 那套灰阶
const LINE_COLORS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed"];

function formatTick(ts: number): string {
  return new Date(ts).toISOString().slice(0, 10);
}

export function YieldCurveChart({
  series,
}: {
  series: { key: string; label: string; points: DataPoint[] }[];
}) {
  const chartConfig: ChartConfig = Object.fromEntries(
    series.map((s, i) => [s.key, { label: s.label, color: LINE_COLORS[i % LINE_COLORS.length] }])
  );

  // 按日期把各期限的序列合并成一行行的宽表：{ ts, "2Y": v, "5Y": v, ... }
  const rowsByTs = new Map<number, Record<string, number>>();
  for (const s of series) {
    for (const p of s.points) {
      const ts = new Date(p.date).getTime();
      const row = rowsByTs.get(ts) ?? { ts };
      row[s.key] = p.value;
      rowsByTs.set(ts, row);
    }
  }
  const rows = Array.from(rowsByTs.values()).sort((a, b) => a.ts - b.ts);

  return (
    <div>
      <ChartContainer config={chartConfig} className="aspect-auto h-[360px] w-full">
        <LineChart data={rows}>
          <CartesianGrid vertical={false} />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatTick}
            tickLine={false}
            axisLine={false}
            minTickGap={48}
          />
          <YAxis tickLine={false} axisLine={false} width={50} domain={["auto", "auto"]} />
          <ChartTooltip
            labelFormatter={(_, payload) => formatTick(payload?.[0]?.payload?.ts)}
            content={<ChartTooltipContent indicator="line" />}
          />
          {series.map((s, i) => (
            <Line
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stroke={LINE_COLORS[i % LINE_COLORS.length]}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ChartContainer>

      {/* Recharts 默认图例的顺序不受调用方控制，这里手写图例，直接按 series 传入的顺序渲染 */}
      <div className="mt-3 flex flex-wrap items-center justify-center gap-4 text-sm">
        {series.map((s, i) => (
          <div key={s.key} className="flex items-center gap-1.5">
            <span
              className="h-2 w-2 shrink-0 rounded-[2px]"
              style={{ backgroundColor: LINE_COLORS[i % LINE_COLORS.length] }}
            />
            <span className="text-muted-foreground">{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
