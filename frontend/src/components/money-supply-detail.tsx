"use client";

import { useEffect, useState } from "react";
import { getIndicatorForecast, getIndicatorHistory } from "@/lib/api";
import { IndicatorDetail } from "@/components/indicator-detail";
import { EconTheory } from "@/components/econ-theory";
import { getGlossaryEntry } from "@/lib/indicator-glossary";
import type { DataPoint, ForecastPoint } from "@/lib/api";

type View = "ABS" | "YOY" | "MOM";

const VIEW_OPTIONS: { key: View; label: string }[] = [
  { key: "ABS", label: "绝对值" },
  { key: "YOY", label: "同比" },
  { key: "MOM", label: "环比" },
];

interface SeriesData {
  name: string;
  unit: string;
  points: DataPoint[];
  forecast: ForecastPoint[];
}

function codeFor(prefix: string, group: string, view: View): string {
  return `${prefix}_${group}_${view}`;
}

export function MoneySupplyDetail({
  codePrefix,
  groups,
  spreadCode,
  spreadName,
  initialGroup,
  initialView,
  initialData,
}: {
  codePrefix: string;
  groups: { key: string; label: string }[];
  /** 有的国家（比如中国）有"剪刀差"这类衍生指标，没有就不传，UI 不会渲染这个 tab */
  spreadCode?: string;
  spreadName?: string;
  initialGroup: string;
  initialView: View;
  initialData: SeriesData;
}) {
  const [group, setGroup] = useState(initialGroup);
  const [view, setView] = useState<View>(initialView);
  const [showSpread, setShowSpread] = useState(false);
  const [loading, setLoading] = useState(false);
  const [cache, setCache] = useState<Record<string, SeriesData>>({
    [codeFor(codePrefix, initialGroup, initialView)]: initialData,
  });
  const [spreadData, setSpreadData] = useState<SeriesData | null>(null);

  const currentCode = codeFor(codePrefix, group, view);
  const current = cache[currentCode];

  useEffect(() => {
    if (showSpread || cache[currentCode]) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getIndicatorHistory(currentCode),
      getIndicatorForecast(currentCode).catch(() => ({ forecast: [] as ForecastPoint[] })),
    ]).then(([h, f]) => {
      if (cancelled) return;
      setCache((c) => ({
        ...c,
        [currentCode]: { name: h.name, unit: h.unit, points: h.points, forecast: f.forecast },
      }));
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [currentCode, cache, showSpread]);

  useEffect(() => {
    if (!showSpread || !spreadCode || spreadData) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getIndicatorHistory(spreadCode),
      getIndicatorForecast(spreadCode).catch(() => ({ forecast: [] as ForecastPoint[] })),
    ]).then(([h, f]) => {
      if (cancelled) return;
      setSpreadData({ name: h.name, unit: h.unit, points: h.points, forecast: f.forecast });
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [showSpread, spreadData, spreadCode]);

  const displayed = showSpread ? spreadData : current;
  const glossary = getGlossaryEntry(showSpread && spreadCode ? spreadCode : currentCode);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        {groups.map((option) => (
          <button
            key={option.key}
            onClick={() => {
              setShowSpread(false);
              setGroup(option.key);
            }}
            className={`rounded-full border px-3 py-1 text-sm transition-colors ${
              !showSpread && group === option.key
                ? "border-foreground bg-foreground text-background"
                : "border-border text-muted-foreground hover:border-foreground/40"
            }`}
          >
            {option.label}
          </button>
        ))}
        {spreadCode && spreadName && (
          <button
            onClick={() => setShowSpread(true)}
            className={`rounded-full border px-3 py-1 text-sm transition-colors ${
              showSpread
                ? "border-foreground bg-foreground text-background"
                : "border-border text-muted-foreground hover:border-foreground/40"
            }`}
          >
            {spreadName}
          </button>
        )}
      </div>

      {!showSpread && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">显示口径：</span>
          {VIEW_OPTIONS.map((option) => (
            <button
              key={option.key}
              onClick={() => setView(option.key)}
              className={`rounded-full border px-3 py-1 text-sm transition-colors ${
                view === option.key
                  ? "border-foreground bg-foreground text-background"
                  : "border-border text-muted-foreground hover:border-foreground/40"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}

      <div className={`mt-6 transition-opacity ${loading ? "opacity-50" : ""}`}>
        {displayed ? (
          <>
            <h2 className="text-sm font-semibold text-muted-foreground">
              {showSpread ? spreadName : displayed.name}（{displayed.unit}）
            </h2>
            {glossary && <p className="mt-1 text-sm text-muted-foreground">{glossary.meaning}</p>}
            <div className="mt-3">
              <IndicatorDetail
                key={showSpread ? spreadCode : currentCode}
                history={displayed.points}
                forecast={displayed.forecast}
              />
            </div>
            {glossary && <EconTheory theory={glossary.theory} />}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">加载中…</p>
        )}
      </div>
    </div>
  );
}
