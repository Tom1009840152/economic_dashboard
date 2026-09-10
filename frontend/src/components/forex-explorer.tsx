"use client";

import { useEffect, useState } from "react";
import {
  getForexForecast,
  getForexHistory,
  type CurrencyOption,
  type DataPoint,
  type ForecastPoint,
} from "@/lib/api";
import { IndicatorDetail } from "@/components/indicator-detail";
import { EconTheory } from "@/components/econ-theory";
import { getForexTheory } from "@/lib/indicator-glossary";

function formatAmount(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

interface PairData {
  history: DataPoint[];
  forecast: ForecastPoint[];
}

interface PairResult {
  data: PairData | null;
  error: string | null;
}

function pairKey(base: string, target: string): string {
  return `${base}-${target}`;
}

export function ForexExplorer({
  currencies,
  initialBase,
  initialTarget,
  initialHistory,
  initialForecast,
}: {
  currencies: CurrencyOption[];
  initialBase: string;
  initialTarget: string;
  initialHistory: DataPoint[];
  initialForecast: ForecastPoint[];
}) {
  const [base, setBase] = useState(initialBase);
  const [target, setTarget] = useState(initialTarget);
  const [inputAmount, setInputAmount] = useState("1");
  const [activeField, setActiveField] = useState<"base" | "target">("base");
  const [pairResults, setPairResults] = useState<Record<string, PairResult>>({
    [pairKey(initialBase, initialTarget)]: {
      data: { history: initialHistory, forecast: initialForecast },
      error: null,
    },
  });

  const currentPairKey = pairKey(base, target);
  const currentResult = pairResults[currentPairKey];
  const history = currentResult?.data?.history ?? [];
  const forecast = currentResult?.data?.forecast ?? [];
  const loading = base !== target && currentResult === undefined;
  const loadError = currentResult?.error ?? null;

  const rate = history.length > 0 ? history[history.length - 1].value : null;
  const latestDate = history.length > 0 ? history[history.length - 1].date : null;
  const prevValue = history.length > 1 ? history[history.length - 2].value : null;
  const changePct = rate !== null && prevValue ? ((rate - prevValue) / prevValue) * 100 : null;

  // 请求结果按货币对缓存。加载状态由当前货币对是否已有结果直接推导，
  // 避免在 effect 开头同步 setState 造成额外渲染。
  useEffect(() => {
    if (base === target || pairResults[currentPairKey]) return;

    let cancelled = false;
    Promise.all([
      getForexHistory(base, target),
      getForexForecast(base, target).catch(() => ({ forecast: [] as ForecastPoint[] })),
    ]).then(([h, f]) => {
      if (cancelled) return;
      setPairResults((results) => ({
        ...results,
        [currentPairKey]: {
          data: { history: h.points, forecast: f.forecast },
          error: null,
        },
      }));
    }).catch(() => {
      if (cancelled) return;
      setPairResults((results) => ({
        ...results,
        [currentPairKey]: { data: null, error: "该货币对暂时加载失败。" },
      }));
    });

    return () => {
      cancelled = true;
    };
  }, [base, target, currentPairKey, pairResults]);

  const numericInput = parseFloat(inputAmount);
  const hasAmount = Number.isFinite(numericInput);
  const amounts = {
    base: activeField === "base"
      ? inputAmount
      : rate !== null && hasAmount ? formatAmount(numericInput / rate) : "",
    target: activeField === "target"
      ? inputAmount
      : rate !== null && hasAmount ? formatAmount(numericInput * rate) : "",
  };

  function handleBaseAmountChange(v: string) {
    setActiveField("base");
    setInputAmount(v);
  }

  function handleTargetAmountChange(v: string) {
    setActiveField("target");
    setInputAmount(v);
  }

  function handleSwap() {
    setBase(target);
    setTarget(base);
    setActiveField((f) => (f === "base" ? "target" : "base"));
  }

  function retryCurrentPair() {
    setPairResults((results) => {
      const next = { ...results };
      delete next[currentPairKey];
      return next;
    });
  }

  const baseName = currencies.find((c) => c.code === base)?.name ?? base;
  const targetName = currencies.find((c) => c.code === target)?.name ?? target;

  return (
    <div>
      {rate !== null && (
        <div>
          <div className="text-sm text-muted-foreground">
            1 {baseName} =
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-semibold tabular-nums">{formatAmount(rate)}</span>
            <span className="text-lg text-muted-foreground">{targetName}</span>
            {changePct !== null && (
              <span
                className={`text-sm tabular-nums ${changePct >= 0 ? "text-emerald-600" : "text-red-600"}`}
              >
                {changePct >= 0 ? "+" : ""}
                {changePct.toFixed(2)}%
              </span>
            )}
          </div>
          {latestDate && (
            <div className="mt-1 text-xs text-muted-foreground">上次更新：{latestDate}</div>
          )}
        </div>
      )}

      <div className="mt-6 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
        <div className="space-y-2">
          <select
            value={base}
            onChange={(e) => setBase(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            {currencies.map((c) => (
              <option key={c.code} value={c.code} disabled={c.code === target}>
                {c.code} - {c.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            value={amounts.base}
            onChange={(e) => handleBaseAmountChange(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm tabular-nums"
          />
        </div>

        <button
          onClick={handleSwap}
          aria-label="交换货币"
          className="rounded-full border border-border p-2 text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
        >
          ⇄
        </button>

        <div className="space-y-2">
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            {currencies.map((c) => (
              <option key={c.code} value={c.code} disabled={c.code === base}>
                {c.code} - {c.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            value={amounts.target}
            onChange={(e) => handleTargetAmountChange(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm tabular-nums"
          />
        </div>
      </div>

      <div className={`mt-6 transition-opacity ${loading ? "opacity-50" : ""}`}>
        {loading ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : loadError ? (
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span>{loadError}</span>
            <button onClick={retryCurrentPair} className="underline underline-offset-2">重试</button>
          </div>
        ) : history.length > 0 ? (
          <IndicatorDetail key={`${base}-${target}`} history={history} forecast={forecast} />
        ) : (
          <p className="text-sm text-muted-foreground">暂无该货币对的数据。</p>
        )}
      </div>

      <EconTheory theory={getForexTheory(base, target)} />
    </div>
  );
}
