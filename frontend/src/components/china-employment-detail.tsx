"use client";

import {
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
import type {
  EmploymentDashboard,
  EmploymentPoint,
  EmploymentSeries,
} from "@/lib/api";

const monthlyConfig = {
  overall: { label: "城镇调查失业率", color: "var(--chart-1)" },
  local: { label: "本地户籍", color: "var(--chart-3)" },
  migrant: { label: "外来户籍", color: "var(--chart-5)" },
} satisfies ChartConfig;

const utilizationConfig = {
  participation: { label: "劳动参与率", color: "var(--chart-3)" },
  employment: { label: "就业人口比", color: "var(--chart-1)" },
} satisfies ChartConfig;

const unemploymentConfig = {
  total: { label: "总体失业率", color: "var(--chart-3)" },
  youth: { label: "青年失业率", color: "var(--chart-5)" },
} satisfies ChartConfig;

function allSeries(data: EmploymentDashboard): EmploymentSeries[] {
  return [...data.monthly_series, ...data.annual_series];
}

function byKey(data: EmploymentDashboard, key: string): EmploymentSeries {
  return allSeries(data).find((series) => series.key === key) ?? {
    key,
    name: key,
    unit: "",
    frequency: "",
    source_type: "",
    scope: "",
    points: [],
  };
}

function latest(data: EmploymentDashboard, key: string): EmploymentPoint | undefined {
  return byKey(data, key).points.at(-1);
}

function pointMap(data: EmploymentDashboard, key: string): Map<string, number> {
  return new Map(byKey(data, key).points.map((point) => [point.period, point.value]));
}

function signed(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function formatPeople(value: number): string {
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 亿人`;
  return `${(value / 10_000).toFixed(0)} 万人`;
}

function previousPeriod(points: EmploymentPoint[], period: string, years: number) {
  const target = `${Number(period.slice(0, 4)) - years}${period.slice(4)}`;
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

export function ChinaEmploymentDetail({ data }: { data: EmploymentDashboard }) {
  const official = data.official_snapshot;
  const urbanUnemployment = byKey(data, "urban_unemployment");
  const latestUrban = urbanUnemployment.points.at(-1);
  const yearAgoUrban = latestUrban
    ? previousPeriod(urbanUnemployment.points, latestUrban.period, 1)
    : undefined;
  const urbanChange = latestUrban && yearAgoUrban ? latestUrban.value - yearAgoUrban.value : null;

  const youth = latest(data, "youth_unemployment_modelled");
  const participation = latest(data, "labor_participation");
  const employmentRatio = latest(data, "employment_ratio");
  const modelledUnemployment = latest(data, "unemployment_modelled");
  const femaleParticipation = latest(data, "female_participation");
  const maleParticipation = latest(data, "male_participation");
  const vulnerable = latest(data, "vulnerable_employment");
  const genderGap = femaleParticipation && maleParticipation
    ? maleParticipation.value - femaleParticipation.value
    : null;
  const participationFiveYearsAgo = participation
    ? previousPeriod(byKey(data, "labor_participation").points, participation.period, 5)
    : undefined;
  const participationChange = participation && participationFiveYearsAgo
    ? participation.value - participationFiveYearsAgo.value
    : null;

  const overallMap = pointMap(data, "urban_unemployment");
  const localMap = pointMap(data, "local_registration_unemployment");
  const migrantMap = pointMap(data, "migrant_registration_unemployment");
  const monthlyPeriods = [...new Set([...overallMap.keys(), ...localMap.keys(), ...migrantMap.keys()])]
    .sort()
    .slice(-60);
  const monthlyRows = monthlyPeriods.map((period) => ({
    period,
    overall: overallMap.get(period),
    local: localMap.get(period),
    migrant: migrantMap.get(period),
  }));

  const participationMap = pointMap(data, "labor_participation");
  const employmentMap = pointMap(data, "employment_ratio");
  const utilizationYears = [...new Set([...participationMap.keys(), ...employmentMap.keys()])].sort();
  const utilizationRows = utilizationYears.map((year) => ({
    year,
    participation: participationMap.get(year),
    employment: employmentMap.get(year),
  }));

  const modelledUnemploymentMap = pointMap(data, "unemployment_modelled");
  const youthMap = pointMap(data, "youth_unemployment_modelled");
  const unemploymentYears = [...new Set([...modelledUnemploymentMap.keys(), ...youthMap.keys()])].sort();
  const unemploymentRows = unemploymentYears.map((year) => ({
    year,
    total: modelledUnemploymentMap.get(year),
    youth: youthMap.get(year),
  }));

  const impliedEmploymentRatio = participation && modelledUnemployment
    ? participation.value * (1 - modelledUnemployment.value / 100)
    : null;
  const urbanShare = official.urban_employment / official.employment_total * 100;
  const ruralShare = official.rural_employment / official.employment_total * 100;

  return (
    <div className="space-y-8">
      {data.warnings.map((warning) => (
        <div key={warning} className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          {warning}
        </div>
      ))}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="城镇调查失业率"
          value={latestUrban ? `${latestUrban.value.toFixed(1)}%` : "--"}
          description={`${latestUrban?.period ?? "--"}；${urbanChange !== null ? `较一年前 ${signed(urbanChange)} 个百分点` : "暂无可比的一年前数据"}。用于判断月度就业拐点。`}
          badge="官方调查"
        />
        <MetricCard
          title="就业人员总量"
          value={formatPeople(official.employment_total)}
          description={`${official.year} 年末官方总量；城镇就业占 ${urbanShare.toFixed(1)}%，适合看就业容量而非月度变化。`}
          badge={`${official.year} 年`}
        />
        <MetricCard
          title="青年失业率"
          value={youth ? `${youth.value.toFixed(1)}%` : "--"}
          description="ILO全国15—24岁模型估计。不能替代统计局16—24岁、不含在校生的月度官方口径。"
          badge="模型估计"
        />
        <MetricCard
          title="周平均工作时间"
          value={`${official.weekly_hours.toFixed(1)} 小时`}
          description={`${official.year} 年官方快照。工时高不必然代表景气，也可能意味着人员偏紧或劳动强度上升。`}
          badge="就业质量"
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.55fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>月度就业景气</CardTitle>
            <CardDescription>同一份城镇劳动力调查中的总体、本地户籍和外来户籍失业率，展示最近五年。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={monthlyConfig} className="h-[350px] w-full" initialDimension={{ width: 760, height: 350 }}>
              <LineChart data={monthlyRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="period" tickLine={false} axisLine={false} minTickGap={30} tickFormatter={(value) => String(value).slice(2)} />
                <YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={["auto", "auto"]} />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, monthlyConfig[name as keyof typeof monthlyConfig]?.label]} />
                <Line dataKey="overall" stroke="var(--color-overall)" strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
                <Line dataKey="local" stroke="var(--color-local)" strokeWidth={1.8} dot={false} connectNulls isAnimationActive={false} />
                <Line dataKey="migrant" stroke="var(--color-migrant)" strokeWidth={1.8} dot={false} connectNulls isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>这一数字没有告诉你的事</CardTitle>
            <CardDescription>调查失业率是必要指标，但不是完整的就业体感。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div>
              <div className="font-medium">退出劳动力市场的人</div>
              <p className="mt-1 text-muted-foreground">没有工作、但不再主动求职的人属于非劳动力，不进入失业率分母。</p>
            </div>
            <div>
              <div className="font-medium">低工时与低收入就业</div>
              <p className="mt-1 text-muted-foreground">参考周工作至少1小时即可被认定为就业，失业率本身无法衡量工作是否充分。</p>
            </div>
            <div>
              <div className="font-medium">全国与城镇并非同一范围</div>
              <p className="mt-1 text-muted-foreground">月度曲线是城镇调查口径；下面的ILO年度序列是全国口径，二者只做交叉验证。</p>
            </div>
            <div>
              <div className="font-medium">青年序列存在断点</div>
              <p className="mt-1 text-muted-foreground">2024年起官方年龄组剔除在校生，不能和此前包含在校生的青年失业率直接连线。</p>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>劳动力如何转化为就业</CardTitle>
            <CardDescription>使用同一套全国15岁以上ILO模型序列，避免跨口径拼接。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg bg-muted/60 p-4 text-center font-mono text-sm leading-7 sm:text-base">
              就业人口比 = 劳动参与率 ×（1 − 失业率）
            </div>
            <div className="mt-5 grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2 text-center">
              <div className="rounded-lg border p-3">
                <div className="text-xs text-muted-foreground">劳动参与率</div>
                <div className="mt-1 text-xl font-semibold tabular-nums">{participation ? `${participation.value.toFixed(1)}%` : "--"}</div>
              </div>
              <div className="text-muted-foreground">×</div>
              <div className="rounded-lg border p-3">
                <div className="text-xs text-muted-foreground">就业概率</div>
                <div className="mt-1 text-xl font-semibold tabular-nums">{modelledUnemployment ? `${(100 - modelledUnemployment.value).toFixed(1)}%` : "--"}</div>
              </div>
              <div className="text-muted-foreground">=</div>
              <div className="rounded-lg border p-3">
                <div className="text-xs text-muted-foreground">隐含就业人口比</div>
                <div className="mt-1 text-xl font-semibold tabular-nums">{impliedEmploymentRatio !== null ? `${impliedEmploymentRatio.toFixed(1)}%` : "--"}</div>
              </div>
            </div>
            <p className="mt-4 text-xs leading-5 text-muted-foreground">
              同源ILO序列中的就业人口比为 {employmentRatio ? `${employmentRatio.value.toFixed(1)}%` : "--"}。舍入和模型过程会造成轻微差异；该恒等式用于定位就业变化来自人口参与还是失业变化。
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{official.year} 年就业结构</CardTitle>
            <CardDescription>官方年度总量快照；城乡是就业所在地结构，不等同于户籍结构。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-5">
              <div>
                <div className="mb-2 flex justify-between text-sm"><span>城镇就业</span><span className="tabular-nums">{formatPeople(official.urban_employment)} · {urbanShare.toFixed(1)}%</span></div>
                <div className="h-3 overflow-hidden rounded-full bg-muted"><div className="h-full bg-chart-1" style={{ width: `${urbanShare}%` }} /></div>
              </div>
              <div>
                <div className="mb-2 flex justify-between text-sm"><span>乡村就业</span><span className="tabular-nums">{formatPeople(official.rural_employment)} · {ruralShare.toFixed(1)}%</span></div>
                <div className="h-3 overflow-hidden rounded-full bg-muted"><div className="h-full bg-chart-3" style={{ width: `${ruralShare}%` }} /></div>
              </div>
              <div className="rounded-lg border p-4">
                <div className="text-sm font-medium">农民工总量 {formatPeople(official.migrant_workers)}</div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">农民工与城镇/乡村就业存在交叉，不能把这三个数字相加。它用于观察流动就业与城乡迁移。</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>参与率与就业人口比</CardTitle>
            <CardDescription>失业率稳定时，就业走弱可能表现为劳动参与率下降。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={utilizationConfig} className="h-[320px] w-full">
              <LineChart data={utilizationRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="year" tickLine={false} axisLine={false} minTickGap={28} />
                <YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={["auto", "auto"]} />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, utilizationConfig[name as keyof typeof utilizationConfig]?.label]} />
                <Line dataKey="participation" stroke="var(--color-participation)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="employment" stroke="var(--color-employment)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>总体与青年就业压力</CardTitle>
            <CardDescription>均为ILO年度模型估计，青年为15—24岁；适合长期与国际比较。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={unemploymentConfig} className="h-[320px] w-full">
              <LineChart data={unemploymentRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="year" tickLine={false} axisLine={false} minTickGap={28} />
                <YAxis tickLine={false} axisLine={false} width={42} unit="%" domain={[0, "auto"]} />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, unemploymentConfig[name as keyof typeof unemploymentConfig]?.label]} />
                <ReferenceLine y={5} stroke="var(--border)" strokeDasharray="4 4" />
                <Line dataKey="total" stroke="var(--color-total)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line dataKey="youth" stroke="var(--color-youth)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <ChartLegend content={<ChartLegendContent />} />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">月度周期变化</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-semibold tabular-nums">{urbanChange !== null ? `${signed(urbanChange)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">城镇调查失业率较一年前变化；上升通常意味着就业景气转弱。</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">参与率五年变化</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-semibold tabular-nums">{participationChange !== null ? `${signed(participationChange)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">劳动参与下降可能掩盖在失业率之外，是人口与就业之间的重要连接。</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">性别参与差距</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-semibold tabular-nums">{genderGap !== null ? `${genderGap.toFixed(1)} pp` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">男性减女性劳动参与率；托育、退休年龄和服务业机会都会影响差距。</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm">脆弱就业占比</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-semibold tabular-nums">{vulnerable ? `${vulnerable.value.toFixed(1)}%` : "--"}</div><p className="mt-2 text-xs text-muted-foreground">自营劳动者和无酬家庭帮工的模型估计占比，不等同于中国官方“灵活就业”。</p></CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>数据可信度与使用边界</CardTitle>
          <CardDescription>页面不合成黑箱“就业健康分”，保留每类数据的来源和局限。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border p-4">
            <Badge>官方抽样调查</Badge>
            <div className="mt-3 font-medium">适合判断月度拐点</div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">覆盖城镇常住人口，定义规范；但无法单独反映退出求职、低收入和就业不充分。</p>
          </div>
          <div className="rounded-lg border p-4">
            <Badge variant="secondary">官方年度统计</Badge>
            <div className="mt-3 font-medium">适合观察总量和结构</div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">就业人数、城乡结构、农民工和工时可信度较高，但更新慢，历史值可能随普查修订。</p>
          </div>
          <div className="rounded-lg border p-4">
            <Badge variant="outline">ILO模型估计</Badge>
            <div className="mt-3 font-medium">适合长期与国际比较</div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">年龄与全国口径更一致，但经过模型补全和平滑，不应用来判断短期政策冲击。</p>
          </div>
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
        <p className="mt-3">青年官方月度序列暂未稳定接入，当前只展示明确标注的ILO年度模型序列；25—59岁旧口径数据保留在接口中用于审计，但不与2024年后的新年龄组连线。</p>
      </div>
    </div>
  );
}
