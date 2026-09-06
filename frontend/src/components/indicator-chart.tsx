"use client";

import { Area, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type { DataPoint, ForecastPoint } from "@/lib/api";

const chartConfig = {
  actual: { label: "历史值", color: "var(--chart-1)" },
  forecastValue: { label: "预测值", color: "var(--chart-2)" },
} satisfies ChartConfig;

interface ChartRow {
  date: string;
  ts: number;
  actual?: number;
  forecastValue?: number;
  lower?: number;
  bandwidth?: number;
}

function formatTick(ts: number): string {
  return new Date(ts).toISOString().slice(0, 10);
}

export function IndicatorChart({
  history,
  forecast,
}: {
  history: DataPoint[];
  forecast: ForecastPoint[];
}) {
  const rows: ChartRow[] = history.map((p) => ({
    date: p.date,
    ts: new Date(p.date).getTime(),
    actual: p.value,
  }));

  const lastActual = history[history.length - 1];
  if (lastActual && forecast.length > 0) {
    rows.push({
      date: lastActual.date,
      ts: new Date(lastActual.date).getTime(),
      actual: lastActual.value,
      forecastValue: lastActual.value,
    });
  }

  for (const f of forecast) {
    rows.push({
      date: f.date,
      ts: new Date(f.date).getTime(),
      forecastValue: f.value,
      lower: f.lower,
      bandwidth: f.upper - f.lower,
    });
  }

  // 用真实时间戳做数值型 X 轴，而不是按行索引均匀分布的类别轴，
  // 这样"按月/按季度/按年"聚合后的稀疏历史点和每日预测点拼在一起时，
  // 时间间隔仍然按真实比例呈现，不会被拉伸变形
  return (
    <ChartContainer config={chartConfig} className="aspect-auto h-[360px] w-full">
      <ComposedChart data={rows}>
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
        <YAxis tickLine={false} axisLine={false} width={70} domain={["auto", "auto"]} />
        <ChartTooltip
          labelFormatter={(_, payload) => formatTick(payload?.[0]?.payload?.ts)}
          content={<ChartTooltipContent indicator="line" />}
        />
        <Area
          dataKey="lower"
          stackId="ci"
          stroke="none"
          fill="transparent"
          isAnimationActive={false}
        />
        <Area
          dataKey="bandwidth"
          stackId="ci"
          stroke="none"
          fill="var(--color-forecastValue)"
          fillOpacity={0.12}
          isAnimationActive={false}
        />
        <Line
          dataKey="actual"
          stroke="var(--color-actual)"
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
        <Line
          dataKey="forecastValue"
          stroke="var(--color-forecastValue)"
          strokeWidth={2}
          strokeDasharray="5 5"
          dot={false}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ChartContainer>
  );
}
