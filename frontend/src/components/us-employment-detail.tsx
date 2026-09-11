"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
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
import type { EmploymentPoint, EmploymentSeries, USEmploymentDashboard } from "@/lib/api";

const slackConfig = {
  u3: { label: "U-3失业率", color: "var(--chart-1)" },
  u6: { label: "U-6利用不足", color: "var(--chart-3)" },
  youth: { label: "16—24岁失业率", color: "var(--chart-5)" },
} satisfies ChartConfig;

const payrollConfig = {
  change: { label: "非农就业月度变化", color: "var(--chart-1)" },
} satisfies ChartConfig;

const participationConfig = {
  participation: { label: "劳动参与率", color: "var(--chart-3)" },
  employment: { label: "就业人口比", color: "var(--chart-1)" },
} satisfies ChartConfig;

const tightnessConfig = {
  ratio: { label: "职位空缺/失业人数", color: "var(--chart-5)" },
} satisfies ChartConfig;

function byKey(data: USEmploymentDashboard, key: string): EmploymentSeries {
  return data.series.find((series) => series.key === key) ?? {
    key,
    name: key,
    unit: "",
    frequency: "",
    source_type: "",
    scope: "",
    points: [],
  };
}

function latest(data: USEmploymentDashboard, key: string): EmploymentPoint | undefined {
  return byKey(data, key).points.at(-1);
}

function pointMap(data: USEmploymentDashboard, key: string): Map<string, number> {
  return new Map(byKey(data, key).points.map((point) => [point.period, point.value]));
}

function signed(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function previousPeriod(points: EmploymentPoint[], period: string, months: number) {
  const date = new Date(`${period}-01T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() - months);
  const target = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
  return points.find((point) => point.period === target);
}

function MetricCard({
  title,
  value,
  description,
  badge,
}: {
  title: string;
  value: string;
  description: string;
  badge: string;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-sm">{title}</CardTitle>
          <Badge variant="outline">{badge}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">{value}</div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  );
}

export function USEmploymentDetail({ data }: { data: USEmploymentDashboard }) {
  const unemployment = latest(data, "unemployment");
  const underemployment = latest(data, "underemployment");
  const youth = latest(data, "youth_unemployment");
  const participation = latest(data, "labor_participation");
  const employmentRatio = latest(data, "employment_ratio");
  const payrollChange = latest(data, "payroll_change");
  const weeklyHours = latest(data, "weekly_hours");
  const wageGrowth = latest(data, "hourly_earnings_yoy");
  const vacancyRatio = latest(data, "vacancy_unemployed_ratio");

  const unemploymentYearAgo = unemployment
    ? previousPeriod(byKey(data, "unemployment").points, unemployment.period, 12)
    : undefined;
  const unemploymentChange = unemployment && unemploymentYearAgo
    ? unemployment.value - unemploymentYearAgo.value
    : null;
  const u6Gap = unemployment && underemployment
    ? underemployment.value - unemployment.value
    : null;
  const youthMultiple = unemployment && youth && unemployment.value > 0
    ? youth.value / unemployment.value
    : null;
  const impliedEmploymentRatio = participation && unemployment
    ? participation.value * (1 - unemployment.value / 100)
    : null;

  const u3Map = pointMap(data, "unemployment");
  const u6Map = pointMap(data, "underemployment");
  const youthMap = pointMap(data, "youth_unemployment");
  const slackPeriods = [...new Set([...u3Map.keys(), ...u6Map.keys(), ...youthMap.keys()])]
    .sort()
    .slice(-180);
  const slackRows = slackPeriods.map((period) => ({
    period,
    u3: u3Map.get(period),
    u6: u6Map.get(period),
    youth: youthMap.get(period),
  }));

  const payrollRows = byKey(data, "payroll_change").points.slice(-60).map((point) => ({
    period: point.period,
    change: point.value,
  }));

  const participationMap = pointMap(data, "labor_participation");
  const employmentMap = pointMap(data, "employment_ratio");
  const participationPeriods = [...new Set([...participationMap.keys(), ...employmentMap.keys()])]
    .sort()
    .slice(-240);
  const participationRows = participationPeriods.map((period) => ({
    period,
    participation: participationMap.get(period),
    employment: employmentMap.get(period),
  }));

  const tightnessRows = byKey(data, "vacancy_unemployed_ratio").points.slice(-180).map((point) => ({
    period: point.period,
    ratio: point.value,
  }));

  const labourSignal = vacancyRatio && vacancyRatio.value >= 1
    ? "职位空缺仍不少于失业人数，劳动力需求尚有韧性。"
    : "职位空缺已少于失业人数，企业招聘需求相对偏弱。";

  return (
    <div className="space-y-8">
      {data.warnings.map((warning) => (
        <div key={warning} className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">{warning}</div>
      ))}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="U-3失业率"
          value={unemployment ? `${unemployment.value.toFixed(1)}%` : "--"}
          description={`${unemployment?.period ?? "--"}，家庭调查；${unemploymentChange !== null ? `较一年前 ${signed(unemploymentChange)} 个百分点` : "暂无同比"}。`}
          badge="家庭调查"
        />
        <MetricCard
          title="非农就业变化"
          value={payrollChange ? `${signed(payrollChange.value, 0)} 千` : "--"}
          description={`${payrollChange?.period ?? "--"}，企业调查；初值通常会在随后两个月修订。`}
          badge="企业调查"
        />
        <MetricCard
          title="劳动参与率"
          value={participation ? `${participation.value.toFixed(1)}%` : "--"}
          description="就业者加积极求职者占16岁以上非机构平民人口的比例，连接人口与就业。"
          badge="劳动力供给"
        />
        <MetricCard
          title="职位空缺/失业人数"
          value={vacancyRatio ? `${vacancyRatio.value.toFixed(2)} 倍` : "--"}
          description={`${vacancyRatio?.period ?? "--"}；${labourSignal}`}
          badge="市场紧张度"
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.55fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>失业与劳动利用不足</CardTitle>
            <CardDescription>U-3看主动求职失业，U-6进一步纳入边缘劳动力和因经济原因兼职者。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={slackConfig} className="h-[350px] w-full" initialDimension={{ width: 760, height: 350 }}>
              <LineChart data={slackRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} />
                <YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={[0, "auto"]} />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, slackConfig[name as keyof typeof slackConfig]?.label]} />
                <Line dataKey="u3" stroke="var(--color-u3)" strokeWidth={2.4} dot={false} isAnimationActive={false} />
                <Line dataKey="u6" stroke="var(--color-u6)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="youth" stroke="var(--color-youth)" strokeWidth={1.8} dot={false} isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>为什么美国要看两份就业调查</CardTitle>
            <CardDescription>它们的调查对象不同，短期背离并不罕见。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div>
              <Badge>家庭调查 CPS</Badge>
              <p className="mt-2 text-muted-foreground">调查个人，产生失业率、劳动参与率和就业人口比；包含自雇者，但抽样误差较大。</p>
            </div>
            <div>
              <Badge variant="secondary">企业调查 CES</Badge>
              <p className="mt-2 text-muted-foreground">调查机构工资单，产生非农就业、工时和工资；覆盖岗位而不是独立个人，不含农场和非注册自雇。</p>
            </div>
            <div>
              <Badge variant="outline">职位空缺调查 JOLTS</Badge>
              <p className="mt-2 text-muted-foreground">观察职位空缺、招聘与离职，发布较慢，并会随年度基准更新修订。</p>
            </div>
            <p className="border-t pt-4 text-xs leading-5 text-muted-foreground">判断拐点时优先看多指标是否同向，而不是要求家庭就业与非农就业每个月完全一致。</p>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>非农就业每月增减</CardTitle>
            <CardDescription>企业调查中的岗位净变化，展示最近五年；零线以下代表当月净减少。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={payrollConfig} className="h-[320px] w-full" initialDimension={{ width: 760, height: 320 }}>
              <BarChart data={payrollRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} />
                <YAxis tickLine={false} axisLine={false} width={50} unit="千" />
                <Tooltip formatter={(value) => [`${signed(Number(value), 0)} 千人`, "非农就业变化"]} />
                <ReferenceLine y={0} stroke="var(--foreground)" />
                <Bar dataKey="change" fill="var(--color-change)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>参与率与就业人口比</CardTitle>
            <CardDescription>失业率可能因人们退出求职而下降，就业人口比能补上这个盲点。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={participationConfig} className="h-[320px] w-full" initialDimension={{ width: 760, height: 320 }}>
              <LineChart data={participationRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} />
                <YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={["auto", "auto"]} />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, participationConfig[name as keyof typeof participationConfig]?.label]} />
                <Line dataKey="participation" stroke="var(--color-participation)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="employment" stroke="var(--color-employment)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.25fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>招聘需求相对失业人口</CardTitle>
            <CardDescription>大于1表示职位空缺数多于失业人数，是判断工资压力和劳动力紧张度的核心比例。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={tightnessConfig} className="h-[300px] w-full" initialDimension={{ width: 760, height: 300 }}>
              <LineChart data={tightnessRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} />
                <YAxis tickLine={false} axisLine={false} width={42} unit="倍" domain={[0, "auto"]} />
                <Tooltip formatter={(value) => [`${Number(value).toFixed(2)} 倍`, "职位空缺/失业人数"]} />
                <ReferenceLine y={1} stroke="var(--foreground)" strokeDasharray="4 4" />
                <Line dataKey="ratio" stroke="var(--color-ratio)" strokeWidth={2.3} dot={false} isAnimationActive={false} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>人口到就业的恒等式</CardTitle>
            <CardDescription>三项均来自同一份家庭调查，可以在统一口径下核对。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg bg-muted/60 p-4 text-center font-mono text-sm leading-7">
              就业人口比 = 劳动参与率 ×（1 − U-3）
            </div>
            <div className="mt-5 grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2 text-center">
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">参与率</div><div className="mt-1 text-xl font-semibold">{participation ? `${participation.value.toFixed(1)}%` : "--"}</div></div>
              <div>×</div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">就业概率</div><div className="mt-1 text-xl font-semibold">{unemployment ? `${(100 - unemployment.value).toFixed(1)}%` : "--"}</div></div>
              <div>=</div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">隐含就业比</div><div className="mt-1 text-xl font-semibold">{impliedEmploymentRatio !== null ? `${impliedEmploymentRatio.toFixed(1)}%` : "--"}</div></div>
            </div>
            <p className="mt-4 text-xs leading-5 text-muted-foreground">家庭调查公布的就业人口比为 {employmentRatio ? `${employmentRatio.value.toFixed(1)}%` : "--"}；轻微差异来自分项舍入。</p>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card><CardHeader><CardTitle className="text-sm">U-6减U-3缺口</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{u6Gap !== null ? `${u6Gap.toFixed(1)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">隐藏在头条失业率之外的边缘劳动力和非自愿兼职压力。</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">青年/总体失业倍数</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{youthMultiple !== null ? `${youthMultiple.toFixed(2)} 倍` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">青年就业通常更具周期性，可作为劳动力市场早期压力信号。</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">平均时薪同比</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{wageGrowth ? `${signed(wageGrowth.value)}%` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">工资压力会影响服务通胀，但平均值也受到就业人员构成变化影响。</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">私营周平均工时</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{weeklyHours ? `${weeklyHours.value.toFixed(1)} 小时` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">企业削减工时往往早于裁员，可作为非农就业的领先补充。</p></CardContent></Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>如何读这组数据</CardTitle>
          <CardDescription>美国就业数据较丰富，但“强”与“弱”仍应按传导链条判断。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border p-4"><div className="font-medium">需求开始转弱</div><p className="mt-2 text-xs leading-5 text-muted-foreground">通常先看到职位空缺/失业人数下降、工时缩短，然后非农增量放缓，最后失业率上升。</p></div>
          <div className="rounded-lg border p-4"><div className="font-medium">供给发生变化</div><p className="mt-2 text-xs leading-5 text-muted-foreground">移民、退休和劳动参与决定可用劳动力。仅看非农就业无法区分需求变化还是供给扩张。</p></div>
          <div className="rounded-lg border p-4"><div className="font-medium">通胀与政策</div><p className="mt-2 text-xs leading-5 text-muted-foreground">职位紧张与工资增长连接服务通胀，进而影响美联储对充分就业和价格稳定的权衡。</p></div>
        </CardContent>
      </Card>

      <div className="rounded-lg border border-dashed p-4 text-xs leading-6 text-muted-foreground">
        <div className="font-medium text-foreground">来源与口径</div>
        {data.sources.map((source) => (
          <div key={source.url} className="mt-2">
            <a href={source.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">{source.name}</a>
            ：{source.description}
          </div>
        ))}
        <p className="mt-3">全部序列均为季节调整后的月度数据。非农就业、职位空缺和工资数据会修订；页面使用最新修订值，不保存初值版本，因此不适合做实时预测误差回测。</p>
      </div>
    </div>
  );
}
