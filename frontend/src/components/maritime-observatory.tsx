"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  Anchor,
  ArrowDownRight,
  ArrowUpRight,
  CalendarClock,
  Container,
  Database,
  ExternalLink,
  Info,
  Layers,
  Minus,
  Route,
  Ship,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type {
  MaritimeChokepoint,
  MaritimeLayer,
  MaritimeMetric,
  MaritimeMetricState,
  MaritimeComparisonDashboard,
  MaritimeComparisonSeries,
  MaritimeObservatoryDashboard,
  MaritimeVesselMix,
} from "@/lib/api";
import { getMaritimeComparison } from "@/lib/api";

type TrendRange = "6m" | "1y" | "3y" | "all";

const TREND_RANGES: Array<{ key: TrendRange; label: string; days: number | null }> = [
  { key: "6m", label: "6个月", days: 183 },
  { key: "1y", label: "1年", days: 365 },
  { key: "3y", label: "3年", days: 365 * 3 },
  { key: "all", label: "全部", days: null },
];

type DashboardRange = "3m" | "6m" | "1y" | "3y" | "custom";

const DASHBOARD_RANGES: Array<{ key: Exclude<DashboardRange, "custom">; label: string; days: number }> = [
  { key: "3m", label: "近3月", days: 92 },
  { key: "6m", label: "近6月", days: 183 },
  { key: "1y", label: "近1年", days: 365 },
  { key: "3y", label: "近3年", days: 365 * 3 },
];

const SERIES_COLORS = ["#0d9488", "#d97706", "#7c3aed", "#dc2626", "#4f46e5"];

const METRIC_STATE: Record<
  MaritimeMetricState,
  { label: string; className: string; icon: typeof ArrowUpRight }
> = {
  stronger: {
    label: "强于同期",
    className:
      "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
    icon: ArrowUpRight,
  },
  steady: {
    label: "大致平稳",
    className:
      "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-200",
    icon: Minus,
  },
  weaker: {
    label: "弱于同期",
    className:
      "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
    icon: ArrowDownRight,
  },
  unavailable: {
    label: "暂不可用",
    className:
      "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300",
    icon: Minus,
  },
};

const LAYER_STATUS: Record<MaritimeLayer["status"], { label: string; className: string }> = {
  available: {
    label: "已接入",
    className:
      "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  },
  pilot: {
    label: "试验中",
    className:
      "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  },
  planned: {
    label: "待接入",
    className:
      "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300",
  },
};

const CHOKEPOINT_STATE: Record<MaritimeChokepoint["state"], { label: string; className: string }> = {
  above: {
    label: "高于同期",
    className: "text-emerald-700 dark:text-emerald-300",
  },
  normal: {
    label: "常态区间",
    className: "text-sky-700 dark:text-sky-300",
  },
  below: {
    label: "低于同期",
    className: "text-amber-700 dark:text-amber-300",
  },
  unavailable: {
    label: "暂不可用",
    className: "text-muted-foreground",
  },
};

function formatNumber(value: number | null, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "--";
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value);
}

function formatPercent(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "--";
  return `${value > 0 ? "+" : ""}${formatNumber(value, 1)}%`;
}

function formatCoverage(value: number): string {
  if (!Number.isFinite(value)) return "--";
  const normalized = value <= 1 ? value * 100 : value;
  return `${Math.max(0, Math.min(normalized, 100)).toFixed(0)}%`;
}

function formatDate(value: string | null): string {
  if (!value) return "暂无日期";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : value;
}

function formatDateTick(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[1].slice(2)}.${match[2]}` : value;
}

function filterTrendRange(
  rows: MaritimeObservatoryDashboard["trend"],
  range: TrendRange,
) {
  const rangeConfig = TREND_RANGES.find((item) => item.key === range);
  if (!rangeConfig?.days || rows.length === 0) return rows;
  const latest = Date.parse(`${rows[rows.length - 1].date}T00:00:00Z`);
  const cutoff = latest - rangeConfig.days * 24 * 60 * 60 * 1000;
  return rows.filter((row) => Date.parse(`${row.date}T00:00:00Z`) >= cutoff);
}

function layerOf(
  data: MaritimeObservatoryDashboard,
  key: MaritimeLayer["key"],
): MaritimeLayer | undefined {
  return data.layers.find((layer) => layer.key === key);
}

function LayerHeading({
  number,
  title,
  eyebrow,
  layer,
  icon: Icon,
}: {
  number: string;
  title: string;
  eyebrow: string;
  layer: MaritimeLayer | undefined;
  icon: typeof Ship;
}) {
  const status = layer ? LAYER_STATUS[layer.status] : LAYER_STATUS.planned;
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border bg-card shadow-sm">
          <Icon className="size-4.5" aria-hidden="true" />
        </span>
        <div>
          <div className="text-[11px] font-medium tracking-[0.18em] text-muted-foreground">
            {number} · {eyebrow}
          </div>
          <h2 className="mt-1 text-lg font-semibold">{title}</h2>
          {layer?.detail && (
            <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">{layer.detail}</p>
          )}
        </div>
      </div>
      <Badge variant="outline" className={status.className}>
        {status.label}
      </Badge>
    </div>
  );
}

function MetricCard({ metric, windowDays }: { metric: MaritimeMetric; windowDays: number }) {
  const state = METRIC_STATE[metric.state];
  const StateIcon = state.icon;
  return (
    <Card className="h-full border-foreground/10 bg-background/85">
      <CardHeader className="gap-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardDescription>{metric.label}</CardDescription>
            <CardTitle className="mt-1.5 text-2xl tabular-nums">
              {formatNumber(metric.value)}
              {metric.value != null && (
                <span className="ml-1.5 text-xs font-normal text-muted-foreground">{metric.unit}</span>
              )}
            </CardTitle>
          </div>
          <Badge variant="outline" className={state.className}>
            <StateIcon className="size-3" aria-hidden="true" />
            {state.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 rounded-lg bg-muted/50 p-3 text-xs">
          <div>
            <div className="text-muted-foreground">同比</div>
            <div className="mt-1 font-semibold tabular-nums">{formatPercent(metric.yoy_pct)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">数据覆盖</div>
            <div className="mt-1 font-semibold tabular-nums">{formatCoverage(metric.coverage)}</div>
          </div>
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">{metric.interpretation}</p>
        <div className="mt-3 border-t pt-2 text-[11px] text-muted-foreground">
          {windowDays}日均值 · 同期可比值 {formatNumber(metric.comparison_value)}{metric.comparison_value != null ? ` ${metric.unit}` : ""}
        </div>
      </CardContent>
    </Card>
  );
}

function VesselMixCard({ vessel }: { vessel: MaritimeVesselMix }) {
  return (
    <div className="rounded-xl border bg-background p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium">{vessel.label}</div>
        <span className="text-xs tabular-nums text-muted-foreground">
          占比 {vessel.share_pct == null ? "--" : `${formatNumber(vessel.share_pct, 1)}%`}
        </span>
      </div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div>
          <span className="text-xl font-semibold tabular-nums">{formatNumber(vessel.current_daily_mn_t)}</span>
          <span className="ml-1 text-[11px] text-muted-foreground">百万吨/日</span>
        </div>
        <span className={`text-xs font-medium tabular-nums ${
          vessel.yoy_pct == null
            ? "text-muted-foreground"
            : vessel.yoy_pct >= 0
              ? "text-emerald-700 dark:text-emerald-300"
              : "text-amber-700 dark:text-amber-300"
        }`}>
          {formatPercent(vessel.yoy_pct)} 同比
        </span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-sky-700 dark:bg-sky-400"
          style={{ width: `${Math.max(0, Math.min(vessel.share_pct ?? 0, 100))}%` }}
        />
      </div>
    </div>
  );
}

function ChokepointCard({ chokepoint }: { chokepoint: MaritimeChokepoint }) {
  const state = CHOKEPOINT_STATE[chokepoint.state];
  return (
    <div className="rounded-xl border bg-background p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 font-medium">
          <Anchor className="size-4 text-muted-foreground" aria-hidden="true" />
          {chokepoint.name}
        </div>
        <span className={`text-xs font-medium ${state.className}`}>{state.label}</span>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">日均通行</dt>
          <dd className="mt-1 font-semibold tabular-nums">
            {formatNumber(chokepoint.current_daily_calls, 1)}
            {chokepoint.current_daily_calls != null && <span className="ml-1 font-normal text-muted-foreground">艘次</span>}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">运力</dt>
          <dd className="mt-1 font-semibold tabular-nums">
            {formatNumber(chokepoint.current_daily_capacity_mn_t)}
            {chokepoint.current_daily_capacity_mn_t != null && <span className="ml-1 font-normal text-muted-foreground">百万吨/日</span>}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">同比</dt>
          <dd className="mt-1 font-semibold tabular-nums">{formatPercent(chokepoint.yoy_pct)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">对比覆盖</dt>
          <dd className="mt-1 font-semibold tabular-nums">{formatCoverage(chokepoint.coverage)}</dd>
        </div>
      </dl>
    </div>
  );
}

function UnavailableObservatory() {
  return (
    <Card className="border-amber-300 dark:border-amber-900">
      <CardContent className="flex items-start gap-3 p-5">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-amber-700 dark:text-amber-300" />
        <div>
          <h2 className="font-semibold">海运观测数据暂时无法加载</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            这表示本次数据接口不可达，不表示海运量为零，也不应据此判断全球贸易转弱。稍后刷新即可重新尝试。
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

type ChartRow = Record<string, string | number | null> & { date: string };

function filterRowsByDate(
  rows: ChartRow[],
  startDate: string | null,
  endDate: string | null,
): ChartRow[] {
  return rows.filter((row) => (
    (!startDate || row.date >= startDate) && (!endDate || row.date <= endDate)
  ));
}

function MultiSeriesChart({
  title,
  description,
  rows,
  series,
}: {
  title: string;
  description: string;
  rows: ChartRow[];
  series: Array<{ code: string; name: string; color: string }>;
}) {
  const config = Object.fromEntries(
    series.map((item) => [item.code, { label: item.name, color: item.color }]),
  ) as ChartConfig;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length > 1 ? (
          <ChartContainer
            config={config}
            className="h-[300px] w-full"
            initialDimension={{ width: 900, height: 300 }}
            role="img"
            aria-label={`${title}折线图`}
          >
            <LineChart data={rows} margin={{ left: 4, right: 10, top: 4 }}>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                minTickGap={36}
                tickFormatter={(value) => formatDateTick(String(value))}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={45}
                tickFormatter={(value) => `${Number(value).toFixed(0)}%`}
              />
              <Tooltip
                labelFormatter={(label) => formatDate(String(label))}
                formatter={(value, name) => [
                  value == null ? "--" : `${Number(value).toFixed(1)}%`,
                  config[String(name)]?.label ?? String(name),
                ]}
              />
              <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="4 4" />
              {series.map((item) => (
                <Line
                  key={item.code}
                  dataKey={item.code}
                  name={item.code}
                  stroke={item.color}
                  strokeWidth={2.2}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
              <ChartLegend content={<ChartLegendContent />} />
            </LineChart>
          </ChartContainer>
        ) : (
          <div className="flex h-[260px] items-center justify-center rounded-xl bg-muted/35 text-sm text-muted-foreground">
            当前筛选区间不足两个有效观察点。
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function mergeSeriesRows(
  series: MaritimeComparisonSeries[],
  metric: "container_yoy" | "exports_yoy" | "inputs_yoy",
): ChartRow[] {
  const rows = new Map<string, ChartRow>();
  for (const item of series) {
    for (const point of item.points) {
      const row = rows.get(point.date) ?? { date: point.date };
      row[item.code] = point[metric];
      rows.set(point.date, row);
    }
  }
  return [...rows.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function MaritimeDataDashboard({ data }: { data: MaritimeObservatoryDashboard }) {
  const defaultCodes = ["CHN", "USA"].filter((code) =>
    data.available_countries.some((option) => option.code === code),
  );
  const [selectedCodes, setSelectedCodes] = useState<string[]>(defaultCodes);
  const [comparison, setComparison] = useState<MaritimeComparisonDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<DashboardRange>("1y");
  const [customStart, setCustomStart] = useState(data.trend_start ?? "");
  const [customEnd, setCustomEnd] = useState(data.as_of ?? "");
  const selectedKey = selectedCodes.join(",");

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getMaritimeComparison(selectedCodes)
        .then((response) => {
          if (!cancelled) {
            setComparison(response);
            setCustomEnd(response.as_of);
          }
        })
        .catch(() => {
          if (!cancelled) setError("所选国家/地区的数据暂时无法加载，请稍后重试。");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selectedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedSeries = comparison?.series ?? [];
  const chartSeries = selectedSeries.map((item, index) => ({
    code: item.code,
    name: item.name,
    color: SERIES_COLORS[index % SERIES_COLORS.length],
  }));

  const bounds = useMemo(() => {
    if (!comparison) return { start: null as string | null, end: null as string | null };
    if (range === "custom") {
      return { start: customStart || null, end: customEnd || comparison.as_of };
    }
    const preset = DASHBOARD_RANGES.find((item) => item.key === range);
    const end = new Date(`${comparison.as_of}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() - (preset?.days ?? 365));
    return { start: end.toISOString().slice(0, 10), end: comparison.as_of };
  }, [comparison, customEnd, customStart, range]);

  const globalRows = filterRowsByDate(
    (comparison?.global_trend ?? []).map((point) => ({
      date: point.date,
      GLOBAL: point.container_yoy,
    })),
    bounds.start,
    bounds.end,
  );
  const containerRows = filterRowsByDate(
    mergeSeriesRows(selectedSeries, "container_yoy"),
    bounds.start,
    bounds.end,
  );
  const exportRows = filterRowsByDate(
    mergeSeriesRows(selectedSeries, "exports_yoy"),
    bounds.start,
    bounds.end,
  );
  const inputRows = filterRowsByDate(
    mergeSeriesRows(selectedSeries, "inputs_yoy"),
    bounds.start,
    bounds.end,
  );

  const toggleGeography = (code: string) => {
    let next = selectedCodes;
    if (selectedCodes.includes(code)) {
      if (selectedCodes.length > 1) next = selectedCodes.filter((item) => item !== code);
    } else if (selectedCodes.length < 5) {
      next = [...selectedCodes, code];
    }
    if (next !== selectedCodes) {
      setLoading(true);
      setError(null);
      setSelectedCodes(next);
    }
  };

  const regions = data.available_countries.filter((option) => option.kind === "region");
  const countries = data.available_countries.filter((option) => option.kind === "country");

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>数据筛选</CardTitle>
          <CardDescription>
            全球曲线固定展示；国家与地区最多选择5项。地区口径直接来自PortWatch官方聚合。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 lg:grid-cols-2">
            {[
              { title: "地区", options: regions },
              { title: "国家", options: countries },
            ].map((group) => (
              <fieldset key={group.title} className="rounded-xl border p-3">
                <legend className="px-1 text-sm font-medium">{group.title}</legend>
                <div className="mt-2 grid max-h-44 grid-cols-2 gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
                  {group.options.map((option) => {
                    const checked = selectedCodes.includes(option.code);
                    const disabled = !checked && selectedCodes.length >= 5;
                    return (
                      <label
                        key={option.code}
                        className={`flex items-center gap-2 rounded-md border px-2.5 py-2 text-xs transition-colors ${
                          checked ? "border-sky-400 bg-sky-50 dark:bg-sky-950/40" : "bg-background"
                        } ${disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer"}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggleGeography(option.code)}
                          className="accent-sky-700"
                        />
                        <span>{option.name}</span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>
            ))}
          </div>

          <div className="flex flex-wrap items-end gap-3 border-t pt-4">
            <div className="flex flex-wrap gap-1.5">
              {DASHBOARD_RANGES.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={range === item.key}
                  onClick={() => setRange(item.key)}
                  className={`rounded-md border px-3 py-2 text-xs transition-colors ${
                    range === item.key
                      ? "border-sky-700 bg-sky-700 text-white dark:border-sky-300 dark:bg-sky-300 dark:text-sky-950"
                      : "bg-background text-muted-foreground hover:border-sky-300 hover:text-foreground"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <label className="text-xs text-muted-foreground">
              开始日期
              <input
                type="date"
                value={customStart}
                min={comparison?.trend_start ?? undefined}
                max={customEnd || comparison?.as_of}
                onChange={(event) => {
                  setCustomStart(event.target.value);
                  setRange("custom");
                }}
                className="mt-1 block h-9 rounded-md border bg-background px-2 text-sm text-foreground"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              结束日期
              <input
                type="date"
                value={customEnd}
                min={customStart || comparison?.trend_start || undefined}
                max={comparison?.as_of}
                onChange={(event) => {
                  setCustomEnd(event.target.value);
                  setRange("custom");
                }}
                className="mt-1 block h-9 rounded-md border bg-background px-2 text-sm text-foreground"
              />
            </label>
            <div className="ml-auto text-xs text-muted-foreground">
              已选 {selectedCodes.length}/5 · 截止 {formatDate(comparison?.as_of ?? data.as_of)}
            </div>
          </div>
        </CardContent>
      </Card>

      {loading && (
        <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">正在加载所选国家与地区的完整历史…</CardContent></Card>
      )}
      {error && (
        <Card className="border-amber-300"><CardContent className="p-5 text-sm text-amber-800 dark:text-amber-200">{error}</CardContent></Card>
      )}

      {!loading && !error && comparison && (
        <>
          <MultiSeriesChart
            title="全球集装箱装卸基准"
            description="固定展示全球WLD聚合，不随国家或地区筛选变化。"
            rows={globalRows}
            series={[{ code: "GLOBAL", name: "全球集装箱装卸", color: "#0369a1" }]}
          />

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {selectedSeries.map((item, index) => (
              <Card key={item.code} className="border-foreground/10">
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-base">{item.name}</CardTitle>
                    <Badge variant="outline">{item.kind === "region" ? "地区" : "国家"}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="grid grid-cols-3 gap-2 text-xs">
                  <div><div className="text-muted-foreground">集装箱</div><div className="mt-1 font-semibold" style={{ color: SERIES_COLORS[index % SERIES_COLORS.length] }}>{formatPercent(item.latest_container_yoy)}</div></div>
                  <div><div className="text-muted-foreground">出口装船</div><div className="mt-1 font-semibold">{formatPercent(item.latest_exports_yoy)}</div></div>
                  <div><div className="text-muted-foreground">大宗投入</div><div className="mt-1 font-semibold">{formatPercent(item.latest_inputs_yoy)}</div></div>
                </CardContent>
              </Card>
            ))}
          </div>

          <MultiSeriesChart
            title="国家与地区：集装箱装卸"
            description="各官方聚合的集装箱进口与出口估算吨位之和，28日均值同比。"
            rows={containerRows}
            series={chartSeries}
          />
          <MultiSeriesChart
            title="国家与地区：全船型出口装船"
            description="观察所选经济体港口出口装船量的方向差异。"
            rows={exportRows}
            series={chartSeries}
          />
          <MultiSeriesChart
            title="国家与地区：大宗投入到港代理"
            description="干散货与油轮进口估算吨位之和，不映射具体商品。"
            rows={inputRows}
            series={chartSeries}
          />

          <div className="rounded-xl border bg-muted/30 p-4 text-xs leading-5 text-muted-foreground">
            {comparison.warnings.map((warning) => <div key={warning}>· {warning}</div>)}
          </div>
        </>
      )}
    </div>
  );
}

export function MaritimeObservatory({
  data,
  failed,
}: {
  data: MaritimeObservatoryDashboard | null;
  failed: boolean;
}) {
  const [activeTab, setActiveTab] = useState<"overview" | "dashboard">("overview");
  const [trendRange, setTrendRange] = useState<TrendRange>("3y");
  if (failed || data == null) return <UnavailableObservatory />;

  const trendConfig = {
    world_flow_yoy: { label: "全球集装箱装卸", color: "#0369a1" },
  } satisfies ChartConfig;
  const quantityLayer = layerOf(data, "quantity");
  const congestionLayer = layerOf(data, "congestion");
  const priceLayer = layerOf(data, "price");
  const statusLabel = data.status === "ok" ? "数据可用" : data.status === "partial" ? "部分可用" : "本次不可用";
  const freshnessLabel = data.freshness === "current" ? "更新正常" : data.freshness === "stale" ? "数据偏旧" : "缺少更新";
  const globalMetrics = data.metrics.filter((metric) => metric.key.startsWith("world_"));
  const worldFlow = globalMetrics.find((metric) => metric.key === "world_container_flow");
  const allChartRows = data.trend.filter((point) => point.world_flow_yoy != null);
  const chartRows = filterTrendRange(allChartRows, trendRange);
  const globalHeadline = `全球集装箱装卸脉冲同比 ${formatPercent(worldFlow?.yoy_pct ?? null)}`;

  return (
    <div className="space-y-10">
      <div
        role="tablist"
        aria-label="全球海运观察页面"
        className="grid max-w-xl grid-cols-2 rounded-xl border bg-muted/35 p-1"
      >
        {[
          { key: "overview" as const, label: "全球总览", detail: "全球数据与整体分析" },
          { key: "dashboard" as const, label: "数据看板", detail: "国家、地区与日期筛选" },
        ].map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`rounded-lg px-3 py-2.5 text-left transition-colors ${
              activeTab === tab.key
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <span className="block text-sm font-medium">{tab.label}</span>
            <span className="mt-0.5 block text-[11px]">{tab.detail}</span>
          </button>
        ))}
      </div>

      {activeTab === "dashboard" ? (
        <MaritimeDataDashboard data={data} />
      ) : (
        <>
      <Card className="overflow-hidden border-sky-950/20 shadow-sm dark:border-sky-300/20">
        <CardHeader className="relative gap-5 overflow-hidden bg-slate-950 text-white sm:p-6 lg:flex lg:flex-row lg:items-start lg:justify-between">
          <div className="pointer-events-none absolute -top-28 -right-16 size-72 rounded-full bg-sky-500/15 blur-3xl" />
          <div className="relative min-w-0 max-w-4xl">
            <div className="mb-3 flex items-center gap-2 text-xs font-medium tracking-[0.18em] text-sky-200/80">
              <Ship className="size-4" aria-hidden="true" />
              实体经济观测站 · 第一组
            </div>
            <CardTitle className="max-w-3xl text-2xl leading-tight text-white sm:text-3xl">
              {globalHeadline}
            </CardTitle>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
              从船舶实际进出港与关键航道通行开始，观察贸易实物量。数量、拥堵与价格分层展示，避免把涨价误写成需求走强。
            </p>
          </div>
          <div className="relative flex max-w-sm shrink-0 flex-wrap gap-2 lg:justify-end">
            <Badge variant="outline" className="border-white/20 bg-white/10 text-white">
              {statusLabel}
            </Badge>
            <Badge variant="outline" className="border-white/20 bg-white/10 text-white">
              {freshnessLabel}
            </Badge>
            <Badge variant="outline" className="border-white/20 bg-white/10 text-white">
              研究性指标
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 p-4 sm:p-5 lg:grid-cols-[1fr_auto] lg:items-center">
          <div className="flex items-start gap-2 text-sm leading-6 text-muted-foreground">
            <Info className="mt-1 size-4 shrink-0" aria-hidden="true" />
            <span>
              当前采用{data.window_days}日滚动窗口，与{data.comparison_days}天前同长度窗口比较。这里是可验证的实验性观测，不是官方贸易统计替代品。
            </span>
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground lg:justify-end">
            <span className="inline-flex items-center gap-1.5">
              <CalendarClock className="size-3.5" aria-hidden="true" />
              截止 {formatDate(data.as_of)}
            </span>
            <span>方法 v{data.methodology_version}</span>
          </div>
        </CardContent>
      </Card>

      {data.status === "unavailable" && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          本次上游数据不可用。页面保留方法、来源与分层状态；所有空值均显示为“--”，不会按零处理。
        </div>
      )}

      <section aria-labelledby="quantity-heading">
        <div id="quantity-heading">
          <LayerHeading
            number="01"
            eyebrow="QUANTITY"
            title="海运实体流量"
            layer={quantityLayer}
            icon={Container}
          />
        </div>

        {globalMetrics.length > 0 ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {globalMetrics.map((metric) => (
              <MetricCard key={metric.key} metric={metric} windowDays={data.window_days} />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="p-5 text-sm text-muted-foreground">本次没有可计算的实体流量指标。</CardContent>
          </Card>
        )}

        <div className="mt-5 grid gap-5 xl:grid-cols-[1.55fr_0.85fr]">
          <Card>
            <CardHeader>
              <CardTitle>实体流量同比脉冲</CardTitle>
              <CardDescription>
                全球集装箱装卸量的完整历史；国家与地区比较已移至“数据看板”。
              </CardDescription>
              <div className="mt-2 flex flex-wrap gap-1.5" aria-label="选择趋势展示区间">
                {TREND_RANGES.map((range) => (
                  <button
                    key={range.key}
                    type="button"
                    onClick={() => setTrendRange(range.key)}
                    className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                      trendRange === range.key
                        ? "border-sky-700 bg-sky-700 text-white dark:border-sky-300 dark:bg-sky-300 dark:text-sky-950"
                        : "bg-background text-muted-foreground hover:border-sky-300 hover:text-foreground"
                    }`}
                    aria-pressed={trendRange === range.key}
                  >
                    {range.label}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent>
              {chartRows.length > 1 ? (
                <>
                  <p className="sr-only">全球集装箱装卸量滚动同比走势。</p>
                  <ChartContainer
                    config={trendConfig}
                    className="h-[310px] w-full"
                    initialDimension={{ width: 820, height: 310 }}
                    role="img"
                    aria-label="全球集装箱装卸滚动同比折线图"
                  >
                    <LineChart data={chartRows} margin={{ left: 4, right: 10, top: 4 }}>
                      <CartesianGrid vertical={false} />
                      <XAxis
                        dataKey="date"
                        tickLine={false}
                        axisLine={false}
                        minTickGap={34}
                        tickFormatter={(value) => formatDateTick(String(value))}
                      />
                      <YAxis
                        tickLine={false}
                        axisLine={false}
                        width={45}
                        tickFormatter={(value) => `${Number(value).toFixed(0)}%`}
                      />
                      <Tooltip
                        labelFormatter={(label) => formatDate(String(label))}
                        formatter={(value, name) => [
                          value == null ? "--" : `${Number(value).toFixed(1)}%`,
                          trendConfig[name as keyof typeof trendConfig]?.label ?? String(name),
                        ]}
                      />
                      <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="4 4" />
                      <Line dataKey="world_flow_yoy" stroke="var(--color-world_flow_yoy)" strokeWidth={2.4} dot={false} connectNulls={false} isAnimationActive={false} />
                      <ChartLegend content={<ChartLegendContent />} />
                    </LineChart>
                  </ChartContainer>
                </>
              ) : (
                <div className="flex h-[260px] items-center justify-center rounded-xl bg-muted/35 text-sm text-muted-foreground">
                  至少积累两个有效观察点后展示趋势。
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Ship className="size-4" aria-hidden="true" />
                <CardTitle>船型结构</CardTitle>
              </div>
              <CardDescription>分解当前实物流量由哪类船舶承载，价格不参与这里的权重。</CardDescription>
            </CardHeader>
            <CardContent>
              {data.vessel_mix.length > 0 ? (
                <div className="space-y-3">
                  {data.vessel_mix.map((vessel) => <VesselMixCard key={vessel.key} vessel={vessel} />)}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">船型结构本次暂不可用。</p>
              )}
            </CardContent>
          </Card>
        </div>
      </section>

      <section aria-labelledby="chokepoint-heading">
        <div id="chokepoint-heading">
          <LayerHeading
            number="02"
            eyebrow="CHOKEPOINTS & REROUTING"
            title="关键咽喉与绕行压力"
            layer={congestionLayer}
            icon={Route}
          />
        </div>

        <Card className="overflow-hidden">
          <CardContent className="p-4 sm:p-5">
            {data.chokepoints.length > 0 ? (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {data.chokepoints.map((chokepoint) => (
                  <ChokepointCard key={chokepoint.key} chokepoint={chokepoint} />
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed p-5 text-sm leading-6 text-muted-foreground">
                咽喉通行数据仍在接入或本次没有有效结果；不会用全球总量反推某一航道的拥堵状态。
              </div>
            )}
            <div className="mt-4 flex items-start gap-2 rounded-lg bg-muted/55 px-3 py-2.5 text-xs leading-5 text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              航道通行量下降不自动等于“拥堵”：也可能来自需求减弱、绕行、天气或数据覆盖变化。等待时间与绕行距离将在验证后单列，当前不混入实体流量。
            </div>
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="price-heading">
        <div id="price-heading">
          <LayerHeading
            number="03"
            eyebrow="FREIGHT PRICE"
            title="运价层：独立对照，不混入流量"
            layer={priceLayer}
            icon={Layers}
          />
        </div>
        <Card className="border-dashed bg-muted/20">
          <CardContent className="grid gap-5 p-5 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <div className="font-medium">为什么先不做“量价合一”</div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                运价既受货量影响，也受运力、燃油、保险、港口等待和绕行冲击影响。把价格直接加进吨位指数，会在供给受阻时把“更贵”误判为“贸易更强”。
              </p>
            </div>
            <div className="rounded-xl border bg-background px-4 py-3 text-sm lg:max-w-xs">
              <div className="font-medium">当前规则</div>
              <div className="mt-1 text-xs leading-5 text-muted-foreground">
                价格只作为未来的独立对照层；完成数据授权、历史回填和样本外验证前，权重固定为 0。
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Database className="size-4" aria-hidden="true" />
              <CardTitle>数据来源与更新</CardTitle>
            </div>
            <CardDescription>来源、频率与访问边界保持可核对，实验指标不隐藏原始出处。</CardDescription>
          </CardHeader>
          <CardContent>
            {data.sources.length > 0 ? (
              <div className="space-y-3">
                {data.sources.map((source) => (
                  <a
                    key={`${source.name}-${source.url}`}
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block rounded-xl border p-3.5 transition-colors hover:border-foreground/30 hover:bg-muted/25"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="font-medium">{source.name}</div>
                      <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                    </div>
                    <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{source.description}</p>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                      <span>更新：{source.update_frequency}</span>
                      <span>访问：{source.access_level}</span>
                    </div>
                  </a>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">来源清单本次暂不可用。</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>方法边界与当前警告</CardTitle>
            <CardDescription>
              AIS与港口进出记录适合高频观察，但不能替代海关贸易金额、提单商品分类或官方国民账户。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              {data.layers.map((layer) => {
                const status = LAYER_STATUS[layer.status];
                return (
                  <div key={layer.key} className="rounded-xl border bg-muted/25 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{layer.title}</span>
                      <Badge variant="outline" className={status.className}>{status.label}</Badge>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">{layer.detail}</p>
                  </div>
                );
              })}
            </div>

            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <div className="flex items-center gap-2 text-sm font-medium">
                <AlertTriangle className="size-4" aria-hidden="true" />
                使用时要保留的限制
              </div>
              {data.warnings.length > 0 ? (
                <ul className="mt-2 space-y-1.5 text-xs leading-5">
                  {data.warnings.map((warning) => <li key={warning}>· {warning}</li>)}
                </ul>
              ) : (
                <p className="mt-2 text-xs leading-5">当前没有额外警告，但本指标仍处于研究验证阶段。</p>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4 text-xs text-muted-foreground">
              <span>方法版本 {data.methodology_version} · 未完成伪实时回测</span>
              <span className="inline-flex items-center gap-1.5">
                <CalendarClock className="size-3.5" aria-hidden="true" />
                当前快照不等于当时可见信息集
              </span>
            </div>
          </CardContent>
        </Card>
      </section>
        </>
      )}
    </div>
  );
}
