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

function formatAmount(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
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
  const [amounts, setAmounts] = useState({ base: "1", target: "" });
  const [activeField, setActiveField] = useState<"base" | "target">("base");
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<DataPoint[]>(initialHistory);
  const [forecast, setForecast] = useState<ForecastPoint[]>(initialForecast);

  const rate = history.length > 0 ? history[history.length - 1].value : null;
  const latestDate = history.length > 0 ? history[history.length - 1].date : null;
  const prevValue = history.length > 1 ? history[history.length - 2].value : null;
  const changePct = rate !== null && prevValue ? ((rate - prevValue) / prevValue) * 100 : null;

  // 拉取新货币对的历史+预测（首屏那一对已经由服务端渲染好了，不用重复请求）
  useEffect(() => {
    if (base === initialBase && target === initialTarget) return;
    if (base === target) {
      setHistory([]);
      setForecast([]);
      return;
    }

    let cancelled = false;
    setLoading(true);
    Promise.all([
      getForexHistory(base, target),
      getForexForecast(base, target).catch(() => ({ forecast: [] as ForecastPoint[] })),
    ]).then(([h, f]) => {
      if (cancelled) return;
      setHistory(h.points);
      setForecast(f.forecast);
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [base, target, initialBase, initialTarget]);

  // 换了货币对、汇率变了之后，按上次是哪个输入框在编辑，重新算另一边的金额
  useEffect(() => {
    if (rate === null) return;
    setAmounts((a) => {
      if (activeField === "base") {
        const n = parseFloat(a.base);
        return { ...a, target: Number.isFinite(n) ? formatAmount(n * rate) : "" };
      }
      const n = parseFloat(a.target);
      return { ...a, base: Number.isFinite(n) ? formatAmount(n / rate) : "" };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rate]);

  function handleBaseAmountChange(v: string) {
    setActiveField("base");
    const n = parseFloat(v);
    setAmounts({ base: v, target: rate !== null && Number.isFinite(n) ? formatAmount(n * rate) : "" });
  }

  function handleTargetAmountChange(v: string) {
    setActiveField("target");
    const n = parseFloat(v);
    setAmounts({ target: v, base: rate !== null && Number.isFinite(n) ? formatAmount(n / rate) : "" });
  }

  function handleSwap() {
    setBase(target);
    setTarget(base);
    setAmounts((a) => ({ base: a.target, target: a.base }));
    setActiveField((f) => (f === "base" ? "target" : "base"));
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
        {history.length > 0 ? (
          <IndicatorDetail key={`${base}-${target}`} history={history} forecast={forecast} />
        ) : (
          <p className="text-sm text-muted-foreground">暂无该货币对的数据。</p>
        )}
      </div>
    </div>
  );
}
