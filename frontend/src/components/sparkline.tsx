"use client";

import { Line, LineChart, ResponsiveContainer } from "recharts";

export function Sparkline({ data, isUp }: { data: number[]; isUp: boolean }) {
  if (data.length < 2) {
    return <div className="h-10 w-24" />;
  }

  const chartData = data.map((value, index) => ({ index, value }));

  return (
    <div className="h-10 w-24">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData}>
          <Line
            type="monotone"
            dataKey="value"
            stroke={isUp ? "var(--color-emerald-600, #059669)" : "var(--color-red-600, #dc2626)"}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
