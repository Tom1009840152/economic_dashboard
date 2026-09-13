"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ArrowRight, CircleGauge, GitBranch, Landmark, Waves } from "lucide-react";
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
  AnalysisSeries,
  MonetaryTransmissionDashboard,
  MonetaryTransmissionSignal,
} from "@/lib/api";

const realRateConfig = {
  policy: { label: "7天逆回购利率", color: "var(--chart-1)" },
  inflation: { label: "核心CPI同比", color: "var(--chart-3)" },
  real: { label: "事后实际政策利率", color: "var(--chart-5)" },
} satisfies ChartConfig;

const liquidityConfig = {
  fdr: { label: "FDR007月均", color: "var(--chart-1)" },
  policy: { label: "7天逆回购利率", color: "var(--chart-3)" },
  gap: { label: "偏离（右轴）", color: "var(--chart-5)" },
} satisfies ChartConfig;

const creditConfig = {
  ratio: { label: "滚动12个月社融/GDP", color: "var(--chart-1)" },
  impulse: { label: "信用脉冲（右轴）", color: "var(--chart-5)" },
} satisfies ChartConfig;

const TONE_STYLES = {
  positive: "border-emerald-200 bg-emerald-50 text-emerald-800",
  neutral: "border-blue-200 bg-blue-50 text-blue-800",
  caution: "border-amber-200 bg-amber-50 text-amber-900",
};

function byKey(data: MonetaryTransmissionDashboard, key: string): AnalysisSeries {
  return data.series.find((series) => series.key === key) ?? {
    key,
    name: key,
    unit: "",
    points: [],
  };
}

function signalByKey(
  data: MonetaryTransmissionDashboard,
  key: string,
): MonetaryTransmissionSignal {
  return data.signals.find((signal) => signal.key === key) ?? {
    key,
    name: key,
    value: 0,
    unit: "",
    period: "--",
    state: "暂无判断",
    interpretation: "暂无可用数据。",
    formula: "--",
  };
}

function pointMap(series: AnalysisSeries): Map<string, number> {
  return new Map(series.points.map((point) => [point.period, point.value]));
}

function combinedRows(
  data: MonetaryTransmissionDashboard,
  definitions: Array<{ source: string; field: string }>,
) {
  const maps = definitions.map(({ source, field }) => ({
    field,
    values: pointMap(byKey(data, source)),
  }));
  const periods = [...new Set(maps.flatMap(({ values }) => [...values.keys()]))].sort();
  return periods.map((period) => ({
    period,
    ...Object.fromEntries(maps.map(({ field, values }) => [field, values.get(period)])),
  }));
}

function valueAt(data: MonetaryTransmissionDashboard, key: string, period: string) {
  return pointMap(byKey(data, key)).get(period);
}

function yearAgo(period: string): string {
  const [year, month] = period.split("-");
  return `${Number(year) - 1}-${month}`;
}

function signed(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function SignalCard({
  signal,
  icon: Icon,
}: {
  signal: MonetaryTransmissionSignal;
  icon: typeof Landmark;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="rounded-xl bg-muted p-2.5">
            <Icon className="size-5" aria-hidden="true" />
          </div>
          <Badge variant="secondary">{signal.state}</Badge>
        </div>
        <CardTitle className="pt-2 text-base">{signal.name}</CardTitle>
        <CardDescription>{signal.period}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">
          {signed(signal.value, signal.unit === "bp" ? 1 : 2)}
          <span className="ml-1 text-sm font-normal text-muted-foreground">{signal.unit}</span>
        </div>
        <div className="mt-4 rounded-lg bg-muted/65 px-3 py-2 font-mono text-xs leading-5">
          {signal.formula}
          {signal.formula_version ? (
            <div className="mt-1 font-sans text-[11px] text-muted-foreground">
              公式 v{signal.formula_version}
              {signal.data_origin
                ? ` · ${signal.data_origin === "stored" ? "版本化存库结果" : "按存库原始数据即时重算"}`
                : ""}
            </div>
          ) : null}
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">
          {signal.interpretation}
        </p>
      </CardContent>
    </Card>
  );
}

export function ChinaMonetaryTransmissionDetail({
  data,
}: {
  data: MonetaryTransmissionDashboard;
}) {
  const realRate = signalByKey(data, "real_policy_rate");
  const liquidity = signalByKey(data, "liquidity_gap");
  const credit = signalByKey(data, "credit_impulse");

  const realRows = combinedRows(data, [
    { source: "policy_rate", field: "policy" },
    { source: "core_cpi", field: "inflation" },
    { source: "real_policy_rate", field: "real" },
  ]);
  const liquidityRows = combinedRows(data, [
    { source: "fdr007", field: "fdr" },
    { source: "liquidity_policy_rate", field: "policy" },
    { source: "liquidity_gap", field: "gap" },
  ]);
  const creditRows = combinedRows(data, [
    { source: "credit_gdp_ratio", field: "ratio" },
    { source: "credit_impulse", field: "impulse" },
  ]).slice(-72);

  const policyValue = valueAt(data, "policy_rate", realRate.period);
  const inflationValue = valueAt(data, "core_cpi", realRate.period);
  const fdrValue = valueAt(data, "fdr007", liquidity.period);
  const liquidityPolicy = valueAt(data, "liquidity_policy_rate", liquidity.period);
  const creditRatio = valueAt(data, "credit_gdp_ratio", credit.period);
  const priorCreditRatio = valueAt(data, "credit_gdp_ratio", yearAgo(credit.period));

  return (
    <div className="space-y-8">
      <Card className="overflow-hidden border-foreground/15 bg-gradient-to-br from-background via-background to-muted/60">
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">
              当前传导判断
            </div>
            <CardTitle className="text-2xl">{data.status}</CardTitle>
            <CardDescription className="mt-2 max-w-3xl text-sm leading-6">
              {data.summary}
            </CardDescription>
          </div>
          <Badge variant="outline" className={TONE_STYLES[data.tone]}>
            最新市场数据 {data.as_of}
          </Badge>
        </CardHeader>
        <CardContent>
          <div className="grid items-center gap-2 rounded-xl border bg-background/75 p-4 text-center sm:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr]">
            <div><div className="font-medium">央行政策利率</div><div className="mt-1 text-xs text-muted-foreground">政策起点</div></div>
            <ArrowRight className="mx-auto size-4 rotate-90 text-muted-foreground sm:rotate-0" />
            <div><div className="font-medium">银行间资金面</div><div className="mt-1 text-xs text-muted-foreground">操作目标</div></div>
            <ArrowRight className="mx-auto size-4 rotate-90 text-muted-foreground sm:rotate-0" />
            <div><div className="font-medium">信用扩张</div><div className="mt-1 text-xs text-muted-foreground">传导中介</div></div>
            <ArrowRight className="mx-auto size-4 rotate-90 text-muted-foreground sm:rotate-0" />
            <div><div className="font-medium">需求与名义增长</div><div className="mt-1 text-xs text-muted-foreground">最终影响</div></div>
          </div>
        </CardContent>
      </Card>

      <section>
        <div className="mb-4">
          <h2 className="text-lg font-semibold">三项可解释信号</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            不做加权总分：价格约束、短端流动性和实体信用可能处于不同阶段。
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          <SignalCard signal={realRate} icon={Landmark} />
          <SignalCard signal={liquidity} icon={Waves} />
          <SignalCard signal={credit} icon={GitBranch} />
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>1. 价格约束：实际政策利率</CardTitle>
            <CardDescription>
              央行名义政策利率扣除核心通胀；零线以上通常意味着正的实际利率。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={realRateConfig} className="h-[330px] w-full" initialDimension={{ width: 760, height: 330 }}>
              <LineChart data={realRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={28} />
                <YAxis tickLine={false} axisLine={false} width={44} unit="%" domain={["auto", "auto"]} />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(2)}%`, realRateConfig[name as keyof typeof realRateConfig]?.label]} />
                <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="4 4" />
                <Line dataKey="policy" stroke="var(--color-policy)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                <Line dataKey="inflation" stroke="var(--color-inflation)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                <Line dataKey="real" stroke="var(--color-real)" strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>当前代入</CardTitle>
            <CardDescription>用同月数据还原看板中的数值。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-xl bg-muted/60 p-5 text-center">
              <div className="font-mono text-sm text-muted-foreground">政策利率 − 核心CPI</div>
              <div className="mt-3 text-xl font-semibold tabular-nums">
                {policyValue?.toFixed(2) ?? "--"}% − {inflationValue?.toFixed(2) ?? "--"}% = {signed(realRate.value)}%
              </div>
            </div>
            <div className="mt-5 space-y-4 text-sm">
              <div>
                <div className="font-medium">它回答什么</div>
                <p className="mt-1 text-muted-foreground">当前政策利率相对已有价格涨幅，是更有刺激性还是更有约束性。</p>
              </div>
              <div>
                <div className="font-medium">它不能回答什么</div>
                <p className="mt-1 text-muted-foreground">企业和居民真正面对的贷款利率、风险溢价与未来通胀预期。</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>2. 操作传导：银行间流动性偏离</CardTitle>
            <CardDescription>
              FDR007月均与7天逆回购利率的差；柱形低于零表示资金价格低于政策利率。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={liquidityConfig} className="h-[330px] w-full" initialDimension={{ width: 760, height: 330 }}>
              <ComposedChart data={liquidityRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} width={44} unit="%" domain={["auto", "auto"]} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} width={48} unit="bp" />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(name === "gap" ? 1 : 3)}${name === "gap" ? " bp" : "%"}`, liquidityConfig[name as keyof typeof liquidityConfig]?.label]} />
                <ReferenceLine yAxisId="right" y={0} stroke="var(--border)" strokeDasharray="4 4" />
                <Bar yAxisId="right" dataKey="gap" fill="var(--color-gap)" opacity={0.28} radius={[3, 3, 0, 0]} isAnimationActive={false} />
                <Line yAxisId="left" dataKey="fdr" stroke="var(--color-fdr)" strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
                <Line yAxisId="left" dataKey="policy" stroke="var(--color-policy)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </ComposedChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>当前代入</CardTitle>
            <CardDescription>月均能降低单日税期、跨季等噪声。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-xl bg-muted/60 p-5 text-center">
              <div className="font-mono text-sm text-muted-foreground">FDR007月均 − 政策利率</div>
              <div className="mt-3 text-xl font-semibold tabular-nums">
                {fdrValue?.toFixed(3) ?? "--"}% − {liquidityPolicy?.toFixed(2) ?? "--"}% = {signed(liquidity.value, 1)} bp
              </div>
            </div>
            <p className="mt-5 text-sm leading-6 text-muted-foreground">
              FDR007是上午DR007成交样本的官方定盘值，不是全天成交量加权的原始DR007。这里将它作为可稳定更新的代理，并明确保留“代理”标签。
            </p>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>3. 实体传导：信用脉冲</CardTitle>
            <CardDescription>
              先把滚动12个月新增社融除以滚动四季度名义GDP，再与一年前比较。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={creditConfig} className="h-[330px] w-full" initialDimension={{ width: 760, height: 330 }}>
              <ComposedChart data={creditRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={30} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} width={44} unit="%" domain={["auto", "auto"]} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} width={46} unit="pp" />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(2)} ${name === "impulse" ? "pp" : "%"}`, creditConfig[name as keyof typeof creditConfig]?.label]} />
                <ReferenceLine yAxisId="right" y={0} stroke="var(--border)" strokeDasharray="4 4" />
                <Bar yAxisId="right" dataKey="impulse" fill="var(--color-impulse)" opacity={0.3} radius={[3, 3, 0, 0]} isAnimationActive={false} />
                <Line yAxisId="left" dataKey="ratio" stroke="var(--color-ratio)" strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </ComposedChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>当前代入</CardTitle>
            <CardDescription>同比差分用于削弱春节月份错位的干扰。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-xl bg-muted/60 p-5 text-center">
              <div className="font-mono text-sm text-muted-foreground">本期信用/GDP − 一年前信用/GDP</div>
              <div className="mt-3 text-xl font-semibold tabular-nums">
                {creditRatio?.toFixed(2) ?? "--"}% − {priorCreditRatio?.toFixed(2) ?? "--"}% = {signed(credit.value)} pp
              </div>
            </div>
            <p className="mt-5 text-sm leading-6 text-muted-foreground">
              脉冲转正表示信用流量相对经济体量在加速，而不是“社融总量为正”。它通常领先需求，但政府债发行、票据冲量与统计口径变化都可能造成扰动。
            </p>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <CircleGauge className="size-5" aria-hidden="true" />
            <CardTitle>如何读出传导阶段</CardTitle>
          </div>
          <CardDescription>顺着链条判断问题发生在哪里，而不是只问“货币松不松”。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border p-4"><div className="font-medium">利率宽松，资金面平稳</div><p className="mt-2 text-xs leading-5 text-muted-foreground">说明央行操作已传到银行间市场；下一步看信用脉冲是否转正。</p></div>
          <div className="rounded-xl border p-4"><div className="font-medium">资金面宽松，信用仍弱</div><p className="mt-2 text-xs leading-5 text-muted-foreground">更像借贷需求、风险偏好或资本约束问题，单纯追加短端流动性的边际作用可能有限。</p></div>
          <div className="rounded-xl border p-4"><div className="font-medium">信用转强，实际利率仍高</div><p className="mt-2 text-xs leading-5 text-muted-foreground">可能来自财政或结构性融资拉动；需要继续验证私人部门融资和名义需求是否同步改善。</p></div>
        </CardContent>
      </Card>

      <div className="space-y-3 rounded-xl border border-dashed p-5 text-xs leading-6 text-muted-foreground">
        <div className="font-medium text-foreground">口径边界</div>
        {data.warnings.map((warning) => <p key={warning}>• {warning}</p>)}
        <div className="border-t pt-3 font-medium text-foreground">来源</div>
        {data.sources.map((source) => (
          <p key={source.url}>
            <a href={source.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">{source.name}</a>
            ：{source.description}
          </p>
        ))}
      </div>
    </div>
  );
}
