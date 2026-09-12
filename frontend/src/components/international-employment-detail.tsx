"use client";

import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartLegend, ChartLegendContent, type ChartConfig } from "@/components/ui/chart";
import type { EmploymentPoint, EmploymentSeries, InternationalEmploymentDashboard } from "@/lib/api";

const unemploymentConfig = {
  unemployment: { label: "总体失业率", color: "var(--chart-1)" },
  youth: { label: "青年失业率", color: "var(--chart-5)" },
} satisfies ChartConfig;

const utilizationConfig = {
  participation: { label: "劳动参与率", color: "var(--chart-3)" },
  employment: { label: "就业率", color: "var(--chart-1)" },
} satisfies ChartConfig;

const genderConfig = {
  male: { label: "男性", color: "var(--chart-1)" },
  female: { label: "女性", color: "var(--chart-5)" },
} satisfies ChartConfig;

type RegionProfile = {
  sourceBadge: string;
  secondaryKey: string;
  secondaryTitle: string;
  secondaryDescription: string;
  genderMaleKey: string;
  genderFemaleKey: string;
  genderGapKey: string;
  genderTitle: string;
  identityComparison: string;
  readingTitle: string;
  readings: Array<{ title: string; body: string }>;
};

const PROFILES: Record<string, RegionProfile> = {
  JP: {
    sourceBadge: "OECD月度可比口径",
    secondaryKey: "youth_unemployment",
    secondaryTitle: "青年失业率",
    secondaryDescription: "青年失业通常更敏感，可用于识别低总体失业率背后的进入门槛。",
    genderMaleKey: "male_participation",
    genderFemaleKey: "female_participation",
    genderGapKey: "gender_participation_gap",
    genderTitle: "男女劳动参与率",
    identityComparison: "同为15—64岁口径，隐含值应与公布失业率接近。",
    readingTitle: "如何读日本劳动力市场",
    readings: [
      { title: "人口收缩", body: "工作年龄人口下降使低失业率可能同时代表需求尚可和劳动力供给不足，不能只按景气强弱解释。" },
      { title: "供给动员", body: "女性、老年人与外籍劳动者的参与变化，是缓冲人口老龄化、支撑潜在增长的关键边际。" },
      { title: "工资与通胀", body: "就业率高并不自动带来工资—物价循环，还需结合春斗工资、工时与服务价格判断。" },
    ],
  },
  KR: {
    sourceBadge: "OECD月度可比口径",
    secondaryKey: "youth_unemployment",
    secondaryTitle: "青年失业率",
    secondaryDescription: "青年与总体失业率的裂口反映就业进入、教育错配与岗位分层。",
    genderMaleKey: "male_participation",
    genderFemaleKey: "female_participation",
    genderGapKey: "gender_participation_gap",
    genderTitle: "男女劳动参与率",
    identityComparison: "同为15—64岁口径，隐含值应与公布失业率接近。",
    readingTitle: "如何读韩国劳动力市场",
    readings: [
      { title: "青年错配", body: "总体失业率较低时，青年失业倍数仍可能揭示优质岗位竞争、教育与产业需求不匹配。" },
      { title: "性别供给", body: "女性参与率和男女差距连接托育、工作制度与可用劳动力，是人口快速老化下的重要缓冲器。" },
      { title: "岗位质量", body: "就业率上升不必然等于收入质量改善，还应结合非正规就业、自雇和工时等结构指标。" },
    ],
  },
  EU: {
    sourceBadge: "Eurostat EA21",
    secondaryKey: "labour_slack",
    secondaryTitle: "劳动力市场闲置率",
    secondaryDescription: "除失业者外，还纳入想工作但暂未积极求职或不能立即上岗等未满足就业需求。",
    genderMaleKey: "male_employment",
    genderFemaleKey: "female_employment",
    genderGapKey: "gender_employment_gap",
    genderTitle: "男女就业率",
    identityComparison: "恒等式采用20—64岁；头条失业率采用15—74岁，因此两者可接近但不要求完全相等。",
    readingTitle: "如何读欧元区劳动力市场",
    readings: [
      { title: "聚合值会遮蔽分化", body: "EA21总失业率可能平稳，但成员国的周期、人口和制度差异很大，判断风险仍需下钻国家分布。" },
      { title: "闲置比失业更宽", body: "劳动力闲置率把潜在求职者和非自愿少工作者纳入，可识别头条失业率遗漏的供给。" },
      { title: "参与与增长", body: "女性、老年人和跨境流动对劳动参与的贡献，会影响工资压力、财政可持续性和潜在增长。" },
    ],
  },
  GB: {
    sourceBadge: "英国ONS",
    secondaryKey: "youth_unemployment",
    secondaryTitle: "青年失业率",
    secondaryDescription: "16—24岁失业率比总体失业率更敏感，可观察青年进入劳动力市场的难度。",
    genderMaleKey: "male_employment",
    genderFemaleKey: "female_employment",
    genderGapKey: "gender_employment_gap",
    genderTitle: "男女就业率",
    identityComparison: "恒等式采用16—64岁就业率和参与率；头条失业率采用16岁以上口径，因此两者可接近但不要求完全相等。",
    readingTitle: "如何读英国劳动力市场",
    readings: [
      { title: "失业与不活跃要并看", body: "英国就业转弱不一定全部进入失业，也可能表现为长期病患等原因造成的经济不活跃上升；只看失业率会低估劳动力退出。" },
      { title: "迁移改变供给", body: "净迁移会较快改变可用劳动力和住房、公共服务需求，应与劳动参与率、就业率及人口年龄结构一起解释。" },
      { title: "工资影响服务通胀", body: "劳动力供给偏紧会推高工资和服务价格，是英格兰银行判断通胀持续性的重要链条；劳动力调查估计值修订与抽样波动也需留意。" },
    ],
  },
};

function byKey(data: InternationalEmploymentDashboard, key: string): EmploymentSeries {
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

function latest(data: InternationalEmploymentDashboard, key: string): EmploymentPoint | undefined {
  return byKey(data, key).points.at(-1);
}

function pointMap(data: InternationalEmploymentDashboard, key: string): Map<string, number> {
  return new Map(byKey(data, key).points.map((point) => [point.period, point.value]));
}

function joinedRows(data: InternationalEmploymentDashboard, first: string, second: string, limit: number) {
  const firstMap = pointMap(data, first);
  const secondMap = pointMap(data, second);
  return [...new Set([...firstMap.keys(), ...secondMap.keys()])]
    .sort()
    .slice(-limit)
    .map((period) => ({ period, first: firstMap.get(period), second: secondMap.get(period) }));
}

function previousYear(points: EmploymentPoint[], period: string): EmploymentPoint | undefined {
  const target = `${Number(period.slice(0, 4)) - 1}${period.slice(4)}`;
  return points.find((point) => point.period === target);
}

function signed(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function MetricCard({ title, value, description, badge }: { title: string; value: string; description: string; badge: string }) {
  return (
    <Card>
      <CardHeader><div className="flex items-start justify-between gap-2"><CardTitle className="text-sm">{title}</CardTitle><Badge variant="outline">{badge}</Badge></div></CardHeader>
      <CardContent><div className="text-3xl font-semibold tabular-nums">{value}</div><p className="mt-2 text-xs leading-5 text-muted-foreground">{description}</p></CardContent>
    </Card>
  );
}

export function InternationalEmploymentDetail({ data }: { data: InternationalEmploymentDashboard }) {
  const profile = PROFILES[data.region];
  const unemployment = latest(data, "unemployment");
  const youth = latest(data, "youth_unemployment");
  const participation = latest(data, "labor_participation");
  const employment = latest(data, "employment_ratio");
  const secondary = latest(data, profile.secondaryKey);
  const genderGap = latest(data, profile.genderGapKey);
  const implied = latest(data, "implied_unemployment");
  const yearAgo = unemployment ? previousYear(byKey(data, "unemployment").points, unemployment.period) : undefined;
  const unemploymentChange = unemployment && yearAgo ? unemployment.value - yearAgo.value : null;
  const youthMultiple = unemployment && youth && unemployment.value > 0 ? youth.value / unemployment.value : null;
  const activationGap = participation && employment ? participation.value - employment.value : null;

  const unemploymentRows = joinedRows(data, "unemployment", "youth_unemployment", 180).map((row) => ({ period: row.period, unemployment: row.first, youth: row.second }));
  const utilizationRows = joinedRows(data, "labor_participation", "employment_ratio", data.region === "EU" ? 60 : 180).map((row) => ({ period: row.period, participation: row.first, employment: row.second }));
  const genderRows = joinedRows(data, profile.genderMaleKey, profile.genderFemaleKey, data.region === "EU" ? 60 : 180).map((row) => ({ period: row.period, male: row.first, female: row.second }));

  return (
    <div className="space-y-8">
      {data.warnings.map((warning) => <div key={warning} className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">{warning}</div>)}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard title="失业率" value={unemployment ? `${unemployment.value.toFixed(1)}%` : "--"} description={`${unemployment?.period ?? "--"}；${unemploymentChange !== null ? `较一年前 ${signed(unemploymentChange)} 个百分点` : "暂无同比"}。`} badge={byKey(data, "unemployment").scope.split("；")[0]} />
        <MetricCard title="就业率" value={employment ? `${employment.value.toFixed(1)}%` : "--"} description={`${employment?.period ?? "--"}；就业人数占对应年龄人口的比例。`} badge={byKey(data, "employment_ratio").frequency} />
        <MetricCard title="劳动参与率" value={participation ? `${participation.value.toFixed(1)}%` : "--"} description={`${participation?.period ?? "--"}；就业者与失业求职者合计占对应年龄人口的比例。`} badge="劳动力供给" />
        <MetricCard title={profile.secondaryTitle} value={secondary ? `${secondary.value.toFixed(1)}%` : "--"} description={`${secondary?.period ?? "--"}；${profile.secondaryDescription}`} badge={profile.sourceBadge} />
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>总体与青年失业</CardTitle><CardDescription>青年失业率相对总体失业率的倍数，可揭示劳动力市场进入难度。</CardDescription></CardHeader>
          <CardContent>
            <ChartContainer config={unemploymentConfig} className="h-[330px] w-full" initialDimension={{ width: 760, height: 330 }}>
              <LineChart data={unemploymentRows}><CartesianGrid vertical={false} /><XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} /><YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={[0, "auto"]} /><Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, unemploymentConfig[name as keyof typeof unemploymentConfig]?.label]} /><Line dataKey="unemployment" stroke="var(--color-unemployment)" strokeWidth={2.4} dot={false} isAnimationActive={false} /><Line dataKey="youth" stroke="var(--color-youth)" strokeWidth={1.9} dot={false} isAnimationActive={false} /><ChartLegend content={<ChartLegendContent />} /></LineChart>
            </ChartContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>参与率与就业率</CardTitle><CardDescription>失业率下降有时来自退出劳动力；把参与率和就业率放在一起，才能区分供给与需求。</CardDescription></CardHeader>
          <CardContent>
            <ChartContainer config={utilizationConfig} className="h-[330px] w-full" initialDimension={{ width: 760, height: 330 }}>
              <LineChart data={utilizationRows}><CartesianGrid vertical={false} /><XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} /><YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={["auto", "auto"]} /><Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, utilizationConfig[name as keyof typeof utilizationConfig]?.label]} /><Line dataKey="participation" stroke="var(--color-participation)" strokeWidth={2} dot={false} isAnimationActive={false} /><Line dataKey="employment" stroke="var(--color-employment)" strokeWidth={2.2} dot={false} isAnimationActive={false} /><ChartLegend content={<ChartLegendContent />} /></LineChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.15fr_1fr]">
        <Card>
          <CardHeader><CardTitle>{profile.genderTitle}</CardTitle><CardDescription>性别差距反映家庭照护、工作制度和可用劳动力尚未充分释放的空间。</CardDescription></CardHeader>
          <CardContent>
            <ChartContainer config={genderConfig} className="h-[310px] w-full" initialDimension={{ width: 760, height: 310 }}>
              <LineChart data={genderRows}><CartesianGrid vertical={false} /><XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={35} tickFormatter={(value) => String(value).slice(2)} /><YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={["auto", "auto"]} /><Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, genderConfig[name as keyof typeof genderConfig]?.label]} /><Line dataKey="male" stroke="var(--color-male)" strokeWidth={2} dot={false} isAnimationActive={false} /><Line dataKey="female" stroke="var(--color-female)" strokeWidth={2} dot={false} isAnimationActive={false} /><ChartLegend content={<ChartLegendContent />} /></LineChart>
            </ChartContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>人口到就业的恒等式</CardTitle><CardDescription>在同年龄口径下，参与人口只有就业和失业两种状态。</CardDescription></CardHeader>
          <CardContent>
            <div className="rounded-lg bg-muted/60 p-4 text-center font-mono text-sm leading-7">隐含失业率 = 1 − 就业率 ÷ 劳动参与率</div>
            <div className="mt-5 grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">参与率</div><div className="mt-1 text-xl font-semibold">{participation ? `${participation.value.toFixed(1)}%` : "--"}</div></div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">就业率</div><div className="mt-1 text-xl font-semibold">{employment ? `${employment.value.toFixed(1)}%` : "--"}</div></div>
              <div className="rounded-lg border p-3"><div className="text-xs text-muted-foreground">隐含失业</div><div className="mt-1 text-xl font-semibold">{implied ? `${implied.value.toFixed(1)}%` : "--"}</div></div>
            </div>
            <p className="mt-4 text-xs leading-5 text-muted-foreground">{profile.identityComparison}</p>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card><CardHeader><CardTitle className="text-sm">失业率同比变化</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{unemploymentChange !== null ? `${signed(unemploymentChange)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">正值表示劳动力市场较一年前转弱，负值表示改善。</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">青年/总体失业倍数</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{youthMultiple !== null ? `${youthMultiple.toFixed(2)} 倍` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">比单看青年失业率更适合跨周期比较进入门槛。</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">参与—就业缺口</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{activationGap !== null ? `${activationGap.toFixed(1)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">对应年龄人口中已进入劳动力但尚未就业的份额。</p></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm">性别差距</CardTitle></CardHeader><CardContent><div className="text-2xl font-semibold tabular-nums">{genderGap ? `${genderGap.value.toFixed(1)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">男性减女性；需结合育儿、就业质量与年龄结构解释。</p></CardContent></Card>
      </section>

      <Card>
        <CardHeader><CardTitle>{profile.readingTitle}</CardTitle><CardDescription>从人口供给、就业利用与结构差异三层判断，而不是只看一个失业率。</CardDescription></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          {profile.readings.map((item) => <div key={item.title} className="rounded-lg border p-4"><div className="font-medium">{item.title}</div><p className="mt-2 text-xs leading-5 text-muted-foreground">{item.body}</p></div>)}
        </CardContent>
      </Card>

      <div className="rounded-lg border border-dashed p-4 text-xs leading-6 text-muted-foreground">
        <div className="font-medium text-foreground">来源与口径</div>
        {data.sources.map((source) => <div key={source.url} className="mt-2"><a href={source.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">{source.name}</a>：{source.description}</div>)}
        <p className="mt-3">不同序列的最新月份可能不同；页面逐项保留原始频率和年龄口径。季调序列会修订，本页使用最新修订值，不保存历史初值。</p>
      </div>
    </div>
  );
}
