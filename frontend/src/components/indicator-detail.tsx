"use client";

import { useMemo, useState } from "react";
import { IndicatorChart } from "@/components/indicator-chart";
import { GRANULARITY_OPTIONS, RANGE_PRESETS, resample, shiftDate, type Granularity } from "@/lib/date-utils";
import type { DataPoint, ForecastPoint } from "@/lib/api";

export function IndicatorDetail({
  history,
  forecast,
}: {
  history: DataPoint[];
  forecast: ForecastPoint[];
}) {
  const minDate = history[0]?.date;
  const maxDate = history[history.length - 1]?.date;

  const [granularity, setGranularity] = useState<Granularity>("day");
  const [startDate, setStartDate] = useState(
    maxDate ? shiftDate(maxDate, 365) : (minDate ?? "")
  );
  const [endDate, setEndDate] = useState(maxDate ?? "");

  function applyPreset(days: number | null) {
    setEndDate(maxDate ?? "");
    setStartDate(days === null ? (minDate ?? "") : shiftDate(maxDate ?? "", days));
  }

  const filteredHistory = useMemo(() => {
    return history.filter((p) => p.date >= startDate && p.date <= endDate);
  }, [history, startDate, endDate]);

  const displayHistory = useMemo(
    () => resample(filteredHistory, granularity),
    [filteredHistory, granularity]
  );

  // 只有查看区间覆盖到最新数据时，才在图上接上预测虚线，避免和查看区间断开显得突兀
  const showForecast = endDate === maxDate;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
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
        <IndicatorChart
          history={displayHistory}
          forecast={showForecast ? forecast : []}
        />
      </div>
    </div>
  );
}
