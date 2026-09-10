"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
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
import { ChartContainer, type ChartConfig } from "@/components/ui/chart";
import type {
  PopulationDashboard,
  PopulationPoint,
  PopulationSeries,
} from "@/lib/api";

const trendConfig = {
  population: { label: "总人口", color: "var(--chart-1)" },
  growth: { label: "人口增长率", color: "var(--chart-4)" },
} satisfies ChartConfig;

const structureConfig = {
  young: { label: "0—14岁", color: "var(--chart-1)" },
  working: { label: "15—64岁", color: "var(--chart-3)" },
  old: { label: "65岁以上", color: "var(--chart-5)" },
} satisfies ChartConfig;

const pyramidConfig = {
  male: { label: "男性", color: "var(--chart-3)" },
  female: { label: "女性", color: "var(--chart-1)" },
} satisfies ChartConfig;

function seriesByKey(data: PopulationDashboard, key: string): PopulationSeries {
  return data.series.find((series) => series.key === key) ?? {
    key,
    name: key,
    unit: "",
    points: [],
  };
}

function latestPoint(data: PopulationDashboard, key: string): PopulationPoint | undefined {
  return seriesByKey(data, key).points.at(-1);
}

function pointMap(data: PopulationDashboard, key: string): Map<number, number> {
  return new Map(seriesByKey(data, key).points.map((point) => [point.year, point.value]));
}

function commonYears(...maps: Map<number, number>[]): number[] {
  if (maps.length === 0) return [];
  return [...maps[0].keys()]
    .filter((year) => maps.every((map) => map.has(year)))
    .sort((a, b) => a - b);
}

function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function formatPopulation(value: number): string {
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)} 万`;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function previousAtLeast(points: PopulationPoint[], latestYear: number, yearsBack: number) {
  return [...points].reverse().find((point) => point.year <= latestYear - yearsBack);
}

function MetricCard({
  title,
  value,
  description,
  year,
}: {
  title: string;
  value: string;
  description: string;
  year?: number;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-sm">{title}</CardTitle>
          {year && <Badge variant="outline">{year}</Badge>}
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">{value}</div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  );
}

export function ChinaPopulationDetail({ data }: { data: PopulationDashboard }) {
  const population = seriesByKey(data, "total_population");
  const populationGrowth = seriesByKey(data, "population_growth");
  const fertility = latestPoint(data, "fertility_rate");
  const oldShare = latestPoint(data, "old_share");
  const workingShare = latestPoint(data, "working_age_share");
  const lifeExpectancy = latestPoint(data, "life_expectancy");
  const dependency = latestPoint(data, "old_dependency_ratio");
  const latestPopulation = population.points.at(-1);
  const latestGrowth = populationGrowth.points.at(-1);

  const populationMap = pointMap(data, "total_population");
  const growthMap = pointMap(data, "population_growth");
  const trendRows = commonYears(populationMap).map((year) => ({
    year,
    population: (populationMap.get(year) ?? 0) / 100_000_000,
    growth: growthMap.get(year),
  }));

  const youngMap = pointMap(data, "young_share");
  const workingMap = pointMap(data, "working_age_share");
  const oldMap = pointMap(data, "old_share");
  const structureRows = commonYears(youngMap, workingMap, oldMap).map((year) => ({
    year,
    young: youngMap.get(year),
    working: workingMap.get(year),
    old: oldMap.get(year),
  }));

  const pyramidRows = [...data.pyramid].reverse().map((row) => ({
    ...row,
    male: -row.male / 1_000_000,
    female: row.female / 1_000_000,
  }));
  const entrantPopulation = data.pyramid
    .filter((row) => row.age_group === "15—19" || row.age_group === "20—24")
    .reduce((sum, row) => sum + row.total, 0);
  const retirementPopulation = data.pyramid
    .filter((row) => row.age_group === "55—59" || row.age_group === "60—64")
    .reduce((sum, row) => sum + row.total, 0);
  const replacementRatio = retirementPopulation > 0 ? entrantPopulation / retirementPopulation : null;

  const oldPoints = seriesByKey(data, "old_share").points;
  const oldPrevious = oldShare ? previousAtLeast(oldPoints, oldShare.year, 10) : undefined;
  const oldShareChange = oldShare && oldPrevious ? oldShare.value - oldPrevious.value : null;

  const workingPopulationPoints = commonYears(populationMap, workingMap).map((year) => ({
    year,
    value: (populationMap.get(year) ?? 0) * (workingMap.get(year) ?? 0) / 100,
  }));
  const workingPopulationLatest = workingPopulationPoints.at(-1);
  const workingPopulationPrevious = workingPopulationLatest
    ? previousAtLeast(workingPopulationPoints, workingPopulationLatest.year, 5)
    : undefined;
  const workingPopulationChange = workingPopulationLatest && workingPopulationPrevious
    ? (workingPopulationLatest.value / workingPopulationPrevious.value - 1) * 100
    : null;

  const birthMap = pointMap(data, "birth_rate");
  const deathMap = pointMap(data, "death_rate");
  const migrationMap = pointMap(data, "net_migration");
  const accountingYears = commonYears(populationMap, birthMap, deathMap, migrationMap)
    .filter((year) => populationMap.has(year - 1));
  const accountingYear = accountingYears.at(-1);
  const actualPopulationChange = accountingYear
    ? (populationMap.get(accountingYear) ?? 0) - (populationMap.get(accountingYear - 1) ?? 0)
    : null;
  const naturalPopulationChange = accountingYear
    ? (populationMap.get(accountingYear - 1) ?? 0)
      * ((birthMap.get(accountingYear) ?? 0) - (deathMap.get(accountingYear) ?? 0)) / 1000
    : null;
  const migrationChange = accountingYear ? (migrationMap.get(accountingYear) ?? 0) : null;
  const statisticalAdjustment = actualPopulationChange !== null
    && naturalPopulationChange !== null
    && migrationChange !== null
    ? actualPopulationChange - naturalPopulationChange - migrationChange
    : null;

  const laborForceMap = pointMap(data, "labor_force");
  const gdpMap = pointMap(data, "real_gdp");
  const growthAccountingYears = commonYears(populationMap, workingMap, laborForceMap, gdpMap);
  const growthYear = growthAccountingYears.at(-1);
  const priorGrowthYear = growthAccountingYears.at(-2);
  let contributions: null | {
    years: number;
    perCapita: number;
    productivity: number;
    employmentIntensity: number;
    demographic: number;
  } = null;
  if (growthYear && priorGrowthYear) {
    const years = growthYear - priorGrowthYear;
    const n0 = populationMap.get(priorGrowthYear) ?? 1;
    const n1 = populationMap.get(growthYear) ?? 1;
    const w0 = n0 * (workingMap.get(priorGrowthYear) ?? 0) / 100;
    const w1 = n1 * (workingMap.get(growthYear) ?? 0) / 100;
    const l0 = laborForceMap.get(priorGrowthYear) ?? 1;
    const l1 = laborForceMap.get(growthYear) ?? 1;
    const y0 = gdpMap.get(priorGrowthYear) ?? 1;
    const y1 = gdpMap.get(growthYear) ?? 1;
    const annualLogChange = (next: number, previous: number) => 100 * Math.log(next / previous) / years;
    contributions = {
      years,
      perCapita: annualLogChange(y1 / n1, y0 / n0),
      productivity: annualLogChange(y1 / l1, y0 / l0),
      employmentIntensity: annualLogChange(l1 / w1, l0 / w0),
      demographic: annualLogChange(w1 / n1, w0 / n0),
    };
  }

  return (
    <div className="space-y-8">
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="总人口"
          value={latestPopulation ? `${(latestPopulation.value / 100_000_000).toFixed(2)} 亿人` : "--"}
          description={`年度增长 ${latestGrowth ? `${signed(latestGrowth.value)}%` : "--"}；总量决定市场和公共服务的规模。`}
          year={latestPopulation?.year}
        />
        <MetricCard
          title="总和生育率"
          value={fertility ? fertility.value.toFixed(2) : "--"}
          description="每名妇女一生平均生育子女数。它影响未来出生队列，但不会立刻改变劳动力。"
          year={fertility?.year}
        />
        <MetricCard
          title="65岁以上占比"
          value={oldShare ? `${oldShare.value.toFixed(1)}%` : "--"}
          description={`老年抚养比 ${dependency ? `${dependency.value.toFixed(1)}%` : "--"}，连接养老、医疗与财政压力。`}
          year={oldShare?.year}
        />
        <MetricCard
          title="劳动年龄人口占比"
          value={workingShare ? `${workingShare.value.toFixed(1)}%` : "--"}
          description={`预期寿命 ${lifeExpectancy ? `${lifeExpectancy.value.toFixed(1)} 岁` : "--"}；健康寿命和退休制度决定有效劳动供给。`}
          year={workingShare?.year}
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.55fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>人口规模与增长速度</CardTitle>
            <CardDescription>总人口看长期规模，增长率看拐点；人口是慢变量，应关注多年趋势。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={trendConfig} className="h-[330px] w-full">
              <ComposedChart data={trendRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="year" tickLine={false} axisLine={false} minTickGap={30} />
                <YAxis yAxisId="population" tickLine={false} axisLine={false} width={45} unit="亿" domain={["auto", "auto"]} />
                <YAxis yAxisId="growth" orientation="right" tickLine={false} axisLine={false} width={42} unit="%" domain={["auto", "auto"]} />
                <Tooltip
                  formatter={(value, name) => [
                    name === "population" ? `${Number(value).toFixed(2)} 亿人` : `${Number(value).toFixed(2)}%`,
                    name === "population" ? "总人口" : "人口增长率",
                  ]}
                />
                <Line yAxisId="population" dataKey="population" stroke="var(--color-population)" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line yAxisId="growth" dataKey="growth" stroke="var(--color-growth)" strokeWidth={2} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>人口增量从哪里来</CardTitle>
            <CardDescription>{accountingYear ? `${accountingYear} 年近似拆解` : "等待共同年份数据"}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg bg-muted/60 p-4 font-mono text-sm leading-7">
              人口增量 = 自然增量 + 净迁移 + 统计调整
            </div>
            {actualPopulationChange !== null && naturalPopulationChange !== null && migrationChange !== null && statisticalAdjustment !== null ? (
              <div className="mt-5 space-y-3 text-sm">
                <div className="flex justify-between gap-3"><span className="text-muted-foreground">实际人口增量</span><span className="font-medium tabular-nums">{formatPopulation(actualPopulationChange)}人</span></div>
                <div className="flex justify-between gap-3"><span className="text-muted-foreground">出生减死亡（估计）</span><span className="tabular-nums">{formatPopulation(naturalPopulationChange)}人</span></div>
                <div className="flex justify-between gap-3"><span className="text-muted-foreground">净迁移（估计）</span><span className="tabular-nums">{formatPopulation(migrationChange)}人</span></div>
                <div className="flex justify-between gap-3 border-t pt-3"><span className="text-muted-foreground">统计口径与调整项</span><span className="tabular-nums">{formatPopulation(statisticalAdjustment)}人</span></div>
                <p className="pt-2 text-xs leading-5 text-muted-foreground">粗出生率、粗死亡率与年中/年末人口的时间口径不同，因此该拆解用于方向判断，不要求机械相等。</p>
              </div>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">暂无能够对齐年份的完整数据。</p>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>年龄结构的长期迁移</CardTitle>
            <CardDescription>结构变化比总人口更早影响教育、就业、住房、医疗和养老需求。</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={structureConfig} className="h-[340px] w-full">
              <AreaChart data={structureRows}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="year" tickLine={false} axisLine={false} minTickGap={30} />
                <YAxis domain={[0, 100]} tickLine={false} axisLine={false} width={40} unit="%" />
                <Tooltip formatter={(value, name) => [`${Number(value).toFixed(1)}%`, structureConfig[name as keyof typeof structureConfig]?.label]} />
                <Area dataKey="young" stackId="age" stroke="var(--color-young)" fill="var(--color-young)" fillOpacity={0.7} isAnimationActive={false} />
                <Area dataKey="working" stackId="age" stroke="var(--color-working)" fill="var(--color-working)" fillOpacity={0.7} isAnimationActive={false} />
                <Area dataKey="old" stackId="age" stroke="var(--color-old)" fill="var(--color-old)" fillOpacity={0.7} isAnimationActive={false} />
              </AreaChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-1">
          <Card>
            <CardHeader><CardTitle className="text-sm">老龄化速度</CardTitle></CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold tabular-nums">{oldShareChange !== null ? `${signed(oldShareChange, 1)} pp` : "--"}</div>
              <p className="mt-2 text-xs text-muted-foreground">65岁以上人口占比过去约10年的变化。</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">劳动年龄人口</CardTitle></CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold tabular-nums">{workingPopulationChange !== null ? `${signed(workingPopulationChange, 1)}%` : "--"}</div>
              <p className="mt-2 text-xs text-muted-foreground">15—64岁人口数量过去约5年的变化。</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">年轻队列替代率</CardTitle></CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold tabular-nums">{replacementRatio !== null ? replacementRatio.toFixed(2) : "--"}</div>
              <p className="mt-2 text-xs text-muted-foreground">15—24岁人口 ÷ 55—64岁人口；低于1意味着年轻队列小于临近退休队列。</p>
            </CardContent>
          </Card>
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>{data.pyramid_year ?? "最新"} 年人口金字塔</CardTitle>
          <CardDescription>左侧为男性、右侧为女性。凸起的年龄队列会依次进入就业、家庭形成和退休阶段。</CardDescription>
        </CardHeader>
        <CardContent>
          <ChartContainer config={pyramidConfig} className="h-[520px] w-full" initialDimension={{ width: 800, height: 520 }}>
            <BarChart data={pyramidRows} layout="vertical" stackOffset="sign" margin={{ left: 10, right: 10 }}>
              <CartesianGrid horizontal={false} />
              <XAxis type="number" tickFormatter={(value) => Math.abs(Number(value)).toFixed(0)} tickLine={false} axisLine={false} />
              <YAxis type="category" dataKey="age_group" tickLine={false} axisLine={false} width={48} />
              <Tooltip formatter={(value, name) => [`${Math.abs(Number(value)).toFixed(1)} 百万人`, name === "male" ? "男性" : "女性"]} />
              <Bar dataKey="male" fill="var(--color-male)" radius={[4, 0, 0, 4]} isAnimationActive={false} />
              <Bar dataKey="female" fill="var(--color-female)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>

      <section className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>人口如何进入人均经济增长</CardTitle>
            <CardDescription>
              使用不变价GDP、劳动力总人数和15—64岁人口做对数增长分解；三项贡献可以相加。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg bg-muted/60 p-4 text-center font-mono text-sm leading-7 sm:text-base">
              ln(Y/N) = ln(Y/L) + ln(L/W) + ln(W/N)
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-center text-xs text-muted-foreground sm:grid-cols-4">
              <div>人均实际GDP</div><div>每名劳动力产出</div><div>劳动参与强度</div><div>劳动年龄占比</div>
            </div>
            {contributions && growthYear && priorGrowthYear ? (
              <div className="mt-6">
                <div className="mb-3 text-sm text-muted-foreground">{priorGrowthYear}—{growthYear} 年均对数增长贡献</div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {[
                    ["人均实际GDP", contributions.perCapita],
                    ["每名劳动力产出", contributions.productivity],
                    ["劳动参与强度", contributions.employmentIntensity],
                    ["人口结构", contributions.demographic],
                  ].map(([label, raw]) => {
                    const value = Number(raw);
                    return (
                      <div key={String(label)} className="rounded-lg border p-3">
                        <div className="text-xs text-muted-foreground">{label}</div>
                        <div className={`mt-1 text-xl font-semibold tabular-nums ${value >= 0 ? "text-emerald-600" : "text-red-600"}`}>
                          {signed(value)} pp
                        </div>
                      </div>
                    );
                  })}
                </div>
                <p className="mt-4 text-xs leading-5 text-muted-foreground">L/W 不是官方劳动参与率：劳动力统计通常覆盖15岁以上，而W固定为15—64岁。该分解是增长核算恒等式，用于定位来源，不代表因果关系。</p>
              </div>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">暂无能够对齐年份的GDP、劳动力与人口数据。</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>从人口结构观察经济</CardTitle>
            <CardDescription>把人口慢变量连接到已有的周期指标。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div><div className="font-medium">潜在增长</div><p className="mt-1 text-muted-foreground">劳动年龄人口 × 劳动参与率 × 就业率 × 工时，再叠加劳动生产率。</p></div>
            <div><div className="font-medium">住房与耐用品</div><p className="mt-1 text-muted-foreground">重点看25—39岁人口、家庭户数和城镇化，而非只看总人口。</p></div>
            <div><div className="font-medium">消费结构</div><p className="mt-1 text-muted-foreground">少儿队列影响教育母婴，老年队列影响医疗、养老与服务消费。</p></div>
            <div><div className="font-medium">财政与利率</div><p className="mt-1 text-muted-foreground">老年抚养比连接养老金、医疗支出和政府债务，但对通胀与利率的方向并非机械确定。</p></div>
          </CardContent>
        </Card>
      </section>

      <div className="rounded-lg border border-dashed p-4 text-xs leading-5 text-muted-foreground">
        口径说明：历史人口及年龄结构来自世界银行 WDI，底层主要采用联合国人口司 WPP 估计；就业数据采用 ILO 模型估计。不同指标的最新年份可能不同，页面在每项数据旁单独标注年份。人口估计会随新普查和新一版模型回溯修订。
      </div>
    </div>
  );
}
