"use client";

import { useMemo, useState } from "react";
import { YieldCurveChart } from "@/components/yield-curve-chart";
import { Sparkline } from "@/components/sparkline";
import { EconTheory } from "@/components/econ-theory";
import { GRANULARITY_OPTIONS, RANGE_PRESETS, resample, shiftDate, type Granularity } from "@/lib/date-utils";
import { BOND_YIELD_THEORY, YIELD_SPREAD_THEORY } from "@/lib/indicator-glossary";
import type { DataPoint } from "@/lib/api";

interface Series {
  key: string;
  label: string;
  points: DataPoint[];
}

export function BondYieldDetail({
  maturities,
  spread,
}: {
  maturities: Series[];
  spread: Series;
}) {
  // 用期限最长的那条序列估算可选的时间范围边界，几条线基本是同一批数据、日期范围一致
  const reference = maturities[maturities.length - 1]?.points ?? [];
  const minDate = reference[0]?.date;
  const maxDate = reference[reference.length - 1]?.date;

  const [granularity, setGranularity] = useState<Granularity>("day");
  const [startDate, setStartDate] = useState(maxDate ? shiftDate(maxDate, 365) : (minDate ?? ""));
  const [endDate, setEndDate] = useState(maxDate ?? "");

  function applyPreset(days: number | null) {
    setEndDate(maxDate ?? "");
    setStartDate(days === null ? (minDate ?? "") : shiftDate(maxDate ?? "", days));
  }

  const displaySeries = useMemo(
    () =>
      maturities.map((s) => ({
        ...s,
        points: resample(
          s.points.filter((p) => p.date >= startDate && p.date <= endDate),
          granularity
        ),
      })),
    [maturities, startDate, endDate, granularity]
  );

  const latestSpread = spread.points[spread.points.length - 1];
  const inverted = (latestSpread?.value ?? 0) < 0;

  return (
    <div>
      <div className="rounded-lg border border-border p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm text-muted-foreground">{spread.label}</div>
            <div className="flex items-baseline gap-2">
              <span
                className={`text-3xl font-semibold tabular-nums ${inverted ? "text-red-600" : "text-foreground"}`}
              >
                {latestSpread ? latestSpread.value.toFixed(2) : "--"}
              </span>
              <span className="text-sm text-muted-foreground">pp</span>
            </div>
            {latestSpread && (
              <div className="mt-1 text-xs text-muted-foreground">
                {latestSpread.date}
                {inverted && <span className="ml-2 text-red-600">曲线倒挂，历史上常被视为衰退预警信号</span>}
              </div>
            )}
          </div>
          <Sparkline data={spread.points.slice(-90).map((p) => p.value)} isUp={!inverted} />
        </div>
      </div>

      <EconTheory title="为什么10年-2年利差重要" theory={YIELD_SPREAD_THEORY} />

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">显示粒度：</span>
        {GRANULARITY_OPTIONS.map((option) => (
          <button
            key={option.key}
            onClick={() => setGranularity(option.key)}
            className={`rounded-full border px-3 py-1 text-sm transition-colors ${
              granularity === option.key
                ? "border-foreground bg-foreground text-background"
                : "border-border text-muted-foreground hover:border-foreground/40"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">时间范围：</span>
        {RANGE_PRESETS.map((preset) => (
          <button
            key={preset.key}
            onClick={() => applyPreset(preset.days)}
            className="rounded-full border border-border px-3 py-1 text-sm text-muted-foreground transition-colors hover:border-foreground/40"
          >
            {preset.label}
          </button>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1.5">
          从
          <input
            type="date"
            value={startDate}
            min={minDate}
            max={endDate || maxDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="rounded-md border border-border px-2 py-1"
          />
        </label>
        <label className="flex items-center gap-1.5">
          到
          <input
            type="date"
            value={endDate}
            min={startDate || minDate}
            max={maxDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="rounded-md border border-border px-2 py-1"
          />
        </label>
      </div>

      <div className="mt-4">
        <YieldCurveChart series={displaySeries} />
      </div>

      <EconTheory title="国债收益率曲线意味着什么" theory={BOND_YIELD_THEORY} />
    </div>
  );
}
