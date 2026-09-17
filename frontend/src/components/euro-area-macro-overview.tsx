import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  Banknote,
  BriefcaseBusiness,
  CalendarClock,
  ChartCandlestick,
  ChevronRight,
  Database,
  Gauge,
  Landmark,
  Minus,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  EUMacroConfidence,
  EUMacroMetric,
  EUMacroOverviewDashboard,
  EUMacroPillar,
  EUMacroTone,
} from "@/lib/api";

const TONE_STYLES: Record<EUMacroTone, string> = {
  positive:
    "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200",
  neutral:
    "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
  caution:
    "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/60 dark:text-amber-200",
  negative:
    "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/60 dark:text-rose-200",
  unavailable:
    "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300",
};

const PILLAR_META: Record<
  EUMacroPillar["key"],
  { icon: LucideIcon; href: string; linkLabel: string }
> = {
  growth: {
    icon: Activity,
    href: "/country/eu/growth",
    linkLabel: "查看增长与景气原始指标",
  },
  labour: {
    icon: BriefcaseBusiness,
    href: "/employment/eu",
    linkLabel: "查看欧元区就业专题",
  },
  inflation: {
    icon: Gauge,
    href: "/country/eu/prices",
    linkLabel: "查看HICP原始指标",
  },
  financial_conditions: {
    icon: ChartCandlestick,
    href: "/country/eu/markets",
    linkLabel: "查看市场与外部代理",
  },
  monetary_policy: {
    icon: Landmark,
    href: "/country/eu/monetary",
    linkLabel: "查看ECB利率与资产负债表",
  },
};

const CONFIDENCE_LABELS: Record<EUMacroConfidence, string> = {
  high: "高覆盖",
  medium: "中等置信",
  low: "低覆盖",
  unavailable: "不可判断",
};

function formatValue(metric: EUMacroMetric) {
  if (metric.value == null) return "--";
  const digits = Math.abs(metric.value) >= 100 ? 1 : 2;
  const value = new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: Math.min(digits, 2),
  }).format(metric.value);
  return `${value}${metric.unit === "%" ? "%" : ` ${metric.unit}`}`;
}

function formatReference(metric: EUMacroMetric) {
  if (metric.reference_value == null || !metric.reference_period) return null;
  const value = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(
    metric.reference_value,
  );
  return `参考 ${metric.reference_period}：${value}${metric.unit === "%" ? "%" : ` ${metric.unit}`}`;
}

function TrendIcon({ metric }: { metric: EUMacroMetric }) {
  if (metric.trend === "up") {
    return <TrendingUp className="size-3.5" aria-label="较参考期上升" />;
  }
  if (metric.trend === "down") {
    return <TrendingDown className="size-3.5" aria-label="较参考期下降" />;
  }
  return <Minus className="size-3.5" aria-label="较参考期基本持平或暂不可比" />;
}

function MetricCell({ metric }: { metric: EUMacroMetric }) {
  const reference = formatReference(metric);
  return (
    <div className="rounded-xl border bg-background/80 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs text-muted-foreground">{metric.label}</div>
          <div className="mt-1.5 flex items-center gap-1.5 text-lg font-semibold tabular-nums">
            {formatValue(metric)}
            {metric.value != null && <TrendIcon metric={metric} />}
          </div>
        </div>
        <div className="text-right text-[11px] leading-4 text-muted-foreground">
          <div>{metric.period ?? "暂无日期"}</div>
          {metric.freshness !== "current" && (
            <div className="mt-1 text-amber-700 dark:text-amber-300">
              {metric.freshness === "stale" ? "数据偏旧" : "当前缺失"}
            </div>
          )}
        </div>
      </div>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">{metric.interpretation}</p>
      {reference && <div className="mt-2 text-[11px] text-muted-foreground">{reference}</div>}
      <details className="mt-2 text-[11px] text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">口径与输入</summary>
        <div className="mt-1.5 space-y-1 border-l pl-2.5">
          <div>{metric.formula}</div>
          <div>输入：{metric.source_codes.join("、")}</div>
        </div>
      </details>
    </div>
  );
}

function PillarCard({ pillar }: { pillar: EUMacroPillar }) {
  const meta = PILLAR_META[pillar.key];
  const Icon = meta.icon;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="gap-3 border-b bg-muted/25">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="rounded-lg border bg-background p-2">
              <Icon className="size-4" aria-hidden="true" />
            </span>
            <div>
              <CardTitle className="text-base">{pillar.title}</CardTitle>
              <div className="mt-1 text-xs text-muted-foreground">
                {CONFIDENCE_LABELS[pillar.confidence]}
              </div>
            </div>
          </div>
          <Badge variant="outline" className={TONE_STYLES[pillar.tone]}>
            {pillar.state_label}
          </Badge>
        </div>
        <p className="text-sm leading-6 text-muted-foreground">{pillar.summary}</p>
      </CardHeader>
      <CardContent className="p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          {pillar.metrics.map((metric) => (
            <MetricCell key={metric.key} metric={metric} />
          ))}
        </div>
        <Link
          href={meta.href}
          className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          {meta.linkLabel}
          <ChevronRight className="size-4" aria-hidden="true" />
        </Link>
      </CardContent>
    </Card>
  );
}

function UnavailableOverview() {
  return (
    <Card className="border-amber-300 dark:border-amber-900">
      <CardContent className="flex items-start gap-3 p-5">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-amber-700 dark:text-amber-300" />
        <div>
          <h2 className="font-semibold">欧元区综合分析暂不可用</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            分析接口本次加载失败。失败不表示欧元区经济处于中性，也不表示相关指标为零；可先查看各分类原始指标。
          </p>
          <Link
            href="/country/eu/growth"
            className="mt-3 inline-flex items-center gap-1 text-sm font-medium hover:underline"
          >
            查看欧元区原始数据
            <ChevronRight className="size-4" aria-hidden="true" />
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export function EuroAreaMacroOverview({
  data,
  failed,
}: {
  data: EUMacroOverviewDashboard | null;
  failed: boolean;
}) {
  if (failed || data == null) return <UnavailableOverview />;

  const downturn = data.downturn_breadth;
  const statusLabel = data.status === "ok" ? "数据完整" : data.status === "partial" ? "部分降级" : "不可用";

  return (
    <div className="space-y-5">
      <Card className="overflow-hidden border-foreground/25 shadow-sm">
        <CardHeader className="gap-5 bg-foreground text-background lg:flex lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 max-w-4xl flex-1">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium tracking-[0.18em] opacity-70">
              <Banknote className="size-4" aria-hidden="true" />
              欧元区宏观简报 · EA21
            </div>
            <CardTitle className="text-2xl leading-tight sm:text-3xl">{data.headline}</CardTitle>
            <p className="mt-3 text-sm leading-6 opacity-75">
              增长、就业、HICP、ECB政策与部分金融条件分别判断；不合成黑箱总分，也不输出未经回测的衰退概率。
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2 lg:max-w-56 lg:justify-end">
            <Badge variant="outline" className="border-background/30 bg-background/10 text-background">
              {statusLabel}
            </Badge>
            <Badge variant="outline" className="border-background/30 bg-background/10 text-background">
              覆盖 {(data.coverage * 100).toFixed(0)}%
            </Badge>
            <Badge variant="outline" className="border-background/30 bg-background/10 text-background">
              最终修订快照
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 p-4 sm:p-5">
          <div className="grid gap-3 text-xs leading-5 text-muted-foreground md:grid-cols-2">
            <div className="rounded-xl border bg-muted/20 p-3.5">
              <span className="font-medium text-foreground">统计范围：</span>
              本页以当前欧元区 EA21 为对象，不混入欧盟 EU27 或单一成员国值；历史序列若采用随成员变化口径，会在来源说明中单独披露。
            </div>
            <div className="rounded-xl border bg-muted/20 p-3.5">
              <span className="font-medium text-foreground">覆盖边界：</span>
              金融条件仅含当前可得的 ECB 资产负债表与市场代理，缺少主权利差、银行信贷和广义货币的完整拼图。
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
            <div className={`rounded-xl border p-4 ${TONE_STYLES[downturn.tone]}`}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-medium tracking-[0.14em] opacity-75">下行证据广度</div>
                  <div className="mt-1 text-lg font-semibold">{downturn.state_label}</div>
                </div>
                <div className="text-2xl font-semibold tabular-nums">
                  {downturn.active_signals}/{downturn.total_signals}
                </div>
              </div>
              <p className="mt-2 text-sm leading-6 opacity-85">{downturn.summary}</p>
              <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <div>
                  <div className="font-medium">已触发</div>
                  <ul className="mt-1 space-y-1 opacity-80">
                    {downturn.triggers.length > 0
                      ? downturn.triggers.map((item) => <li key={item}>· {item}</li>)
                      : <li>· 暂无已触发信号</li>}
                  </ul>
                </div>
                <div>
                  <div className="font-medium">抵消证据</div>
                  <ul className="mt-1 space-y-1 opacity-80">
                    {downturn.offsets.length > 0
                      ? downturn.offsets.slice(0, 3).map((item) => <li key={item}>· {item}</li>)
                      : <li>· 暂无足够抵消证据</li>}
                  </ul>
                </div>
              </div>
            </div>

            <div className="rounded-xl border bg-muted/25 p-4">
              <div className="flex items-center gap-2 text-sm font-medium">
                <CalendarClock className="size-4" aria-hidden="true" />
                各自观察期
              </div>
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                <dt className="text-muted-foreground">市场</dt>
                <dd className="text-right tabular-nums">{data.freshness.market_observation_date ?? "--"}</dd>
                <dt className="text-muted-foreground">月度宏观</dt>
                <dd className="text-right tabular-nums">{data.freshness.monthly_observation_period ?? "--"}</dd>
                <dt className="text-muted-foreground">就业</dt>
                <dd className="text-right tabular-nums">{data.freshness.employment_observation_period ?? "--"}</dd>
                <dt className="text-muted-foreground">季度GDP</dt>
                <dd className="text-right tabular-nums">{data.freshness.quarterly_observation_period ?? "--"}</dd>
              </dl>
              <p className="mt-3 border-t pt-3 text-xs leading-5 text-muted-foreground">
                Eurostat、ECB与市场数据发布节奏不同，因此保留各自截止日期，不强行拼成同一天的“当前值”。
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="columns-1 gap-5 xl:columns-2">
        {data.pillars.map((pillar) => (
          <div key={pillar.key} className="mb-5 inline-block w-full break-inside-avoid">
            <PillarCard pillar={pillar} />
          </div>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">ECB政策到实体经济：当前传导链</CardTitle>
          <p className="text-sm text-muted-foreground">
            用政策利率、资产负债表、市场代理和实体指标组织证据；相关性不等于确定因果。
          </p>
        </CardHeader>
        <CardContent>
          <ol className="grid gap-3 lg:grid-cols-5">
            {data.transmission.map((step, index) => (
              <li key={step.key} className="relative rounded-xl border bg-muted/20 p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    {String(index + 1).padStart(2, "0")} · {step.title}
                  </span>
                  {index < data.transmission.length - 1 && (
                    <ChevronRight className="hidden size-4 text-muted-foreground lg:absolute lg:top-1/2 lg:-right-2.5 lg:block lg:-translate-y-1/2" />
                  )}
                </div>
                <Badge variant="outline" className={`mt-2 ${TONE_STYLES[step.tone]}`}>
                  {step.state_label}
                </Badge>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">{step.detail}</p>
                <div className="mt-2 text-[11px] tabular-nums text-muted-foreground">
                  {step.periods.join(" · ") || "暂无观察期"}
                </div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">下一批数据重点看什么</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3 text-sm leading-6 text-muted-foreground">
              {data.watch_items.map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="mt-2 size-1.5 shrink-0 rounded-full bg-foreground" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">方法与边界</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm leading-6 text-muted-foreground">
            <p>{data.methodology_note}</p>
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3.5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <div className="flex items-center gap-2 font-medium">
                <AlertTriangle className="size-4" aria-hidden="true" />
                当前限制
              </div>
              <ul className="mt-2 space-y-1.5 text-xs leading-5">
                {data.warnings.map((warning) => <li key={warning}>· {warning}</li>)}
              </ul>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4 text-xs">
              <span>方法版本 {data.methodology_version} · 置信度 {CONFIDENCE_LABELS[data.confidence]}</span>
              <Link
                href={data.data_source_path}
                className="inline-flex items-center gap-1.5 font-medium text-foreground hover:underline"
              >
                <Database className="size-3.5" aria-hidden="true" />
                查看欧元区数据来源与更新
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
