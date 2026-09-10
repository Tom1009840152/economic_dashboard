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

interface SeriesResult {
  data: SeriesData | null;
  error: string | null;
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
  const [cache, setCache] = useState<Record<string, SeriesResult>>({
    [codeFor(codePrefix, initialGroup, initialView)]: { data: initialData, error: null },
  });

  const currentCode = codeFor(codePrefix, group, view);
  const requestedCode = showSpread && spreadCode ? spreadCode : currentCode;
  const currentResult = cache[requestedCode];
  const displayed = currentResult?.data ?? null;
  const loading = currentResult === undefined;
  const loadError = currentResult?.error ?? null;

  // 普通货币供应指标与剪刀差走同一个按 code 缓存的加载流程。
  // loading 由缓存中是否已有结果推导，不需要在 effect 中同步设置。
  useEffect(() => {
    if (cache[requestedCode]) return;
    let cancelled = false;
    Promise.all([
      getIndicatorHistory(requestedCode),
      getIndicatorForecast(requestedCode).catch(() => ({ forecast: [] as ForecastPoint[] })),
    ]).then(([h, f]) => {
      if (cancelled) return;
      setCache((c) => ({
        ...c,
        [requestedCode]: {
          data: { name: h.name, unit: h.unit, points: h.points, forecast: f.forecast },
          error: null,
        },
      }));
    }).catch(() => {
      if (cancelled) return;
      setCache((c) => ({
        ...c,
        [requestedCode]: { data: null, error: "该指标暂时加载失败。" },
      }));
    });
    return () => {
      cancelled = true;
    };
  }, [cache, requestedCode]);

  const glossary = getGlossaryEntry(showSpread && spreadCode ? spreadCode : currentCode);

  function retryRequestedCode() {
    setCache((entries) => {
      const next = { ...entries };
      delete next[requestedCode];
      return next;
    });
  }

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
        {loading ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : loadError ? (
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span>{loadError}</span>
            <button onClick={retryRequestedCode} className="underline underline-offset-2">重试</button>
          </div>
        ) : displayed ? (
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
          <p className="text-sm text-muted-foreground">暂无该指标的数据。</p>
        )}
      </div>
    </div>
  );
}
