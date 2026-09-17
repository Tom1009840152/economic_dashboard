import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Database,
  FileClock,
  Layers3,
  RefreshCw,
  ShieldCheck,
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
  getChinaMonetaryTransmission,
  getEmployment,
  getIndicatorCatalog,
  getIndicators,
  getInternationalEmployment,
  getPopulation,
  getRefreshStatus,
  type EmploymentSource,
  type IndicatorCatalogEntry,
  type IndicatorSummary,
  type RefreshResult,
} from "@/lib/api";
import {
  FREQUENCY_LABELS,
  REGION_LABELS,
  DATA_SOURCE_REGIONS,
  DATA_SOURCE_REGION_BY_SLUG,
  SOURCE_REGISTRY_BY_KEY,
  type DataSourceRegion,
  type DataSourceRegionMeta,
  type SourceKind,
} from "@/lib/data-source-registry";

export const dynamic = "force-dynamic";

interface DataSourcesPageProps {
  params: Promise<{ region: string }>;
}

export async function generateMetadata(
  props: DataSourcesPageProps,
): Promise<Metadata> {
  const { region } = await props.params as { region: string };
  const meta = DATA_SOURCE_REGION_BY_SLUG.get(region as DataSourceRegion);
  return {
    title: `${meta?.label ?? "地区"}数据来源与更新｜经济学看板`,
    description: `${meta?.label ?? "地区"}经济数据的出处、发布频率、更新状态与口径说明`,
  };
}

const POLICY_RATE_FALLBACK = {
  value: 1.4,
  effectiveDate: "2025-05-08",
  verifiedThrough: "2026-09-17",
  sourceUrl:
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2026091708554047079/index.html",
};

const KIND_LABELS: Record<SourceKind, string> = {
  official: "官方原始来源",
  international: "官方公共数据库",
  distributor: "数据分发渠道",
  derived: "项目派生",
};

const KIND_ORDER: Record<SourceKind, number> = {
  official: 0,
  international: 1,
  distributor: 2,
  derived: 3,
};

const KIND_STYLES: Record<SourceKind, string> = {
  official: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  international: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200",
  distributor: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200",
  derived: "border-violet-200 bg-violet-50 text-violet-800 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-200",
};

function formatDateTime(value: string | null | undefined) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function lagLabel(months: number) {
  if (months <= 0) return "短滞后（目录记为 0 个月）";
  return `典型约 ${months} 个月`;
}

function lagRange(entries: IndicatorCatalogEntry[]) {
  const lags = entries.map((entry) => entry.release_lag_months);
  const min = Math.min(...lags);
  const max = Math.max(...lags);
  if (min === max) return lagLabel(min);
  return `典型约 ${min}–${max} 个月`;
}

function frequencySummary(entries: IndicatorCatalogEntry[]) {
  const frequencies = Array.from(new Set(entries.map((entry) => entry.frequency)));
  return frequencies.map((frequency) => FREQUENCY_LABELS[frequency] ?? frequency).join("、");
}

function freshnessStyle(freshness?: string) {
  if (freshness === "current" || freshness === "event") {
    return "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200";
  }
  if (freshness === "delayed" || freshness === "stale") {
    return "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200";
  }
  return "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300";
}

function refreshStatusLabel(status?: string) {
  if (status === "success") return "刷新成功";
  if (status === "no_change") return "源端无新值";
  if (status === "failed") return "本轮失败";
  return "暂无运行记录";
}

function SourceLink({ href, children }: { href?: string; children: React.ReactNode }) {
  if (!href) return <span>{children}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 underline decoration-zinc-300 underline-offset-4 hover:decoration-foreground"
    >
      {children}
      <ArrowUpRight aria-hidden="true" className="size-3.5" />
    </a>
  );
}

interface RegionalEmploymentSourceSummary {
  latestMonth: string | null;
  annualUpdated: string | null;
  seriesCount: number;
  sources: EmploymentSource[];
}

async function loadRegionalEmployment(
  meta: DataSourceRegionMeta,
): Promise<RegionalEmploymentSourceSummary | null> {
  if (meta.slug === "global") return null;
  if (meta.slug === "cn") {
    const employment = await getEmployment(meta.apiRegion);
    return {
      latestMonth: employment.latest_month,
      annualUpdated: employment.wdi_updated,
      seriesCount: employment.monthly_series.length + employment.annual_series.length,
      sources: employment.sources,
    };
  }
  const employment = await getInternationalEmployment(meta.apiRegion);
  return {
    latestMonth: employment.latest_month,
    annualUpdated: null,
    seriesCount: employment.series.length,
    sources: employment.sources,
  };
}

export default async function DataSourcesPage(
  props: DataSourcesPageProps,
) {
  const { region } = await props.params as { region: string };
  const regionMeta = DATA_SOURCE_REGION_BY_SLUG.get(region as DataSourceRegion);
  if (!regionMeta) notFound();

  const isChina = regionMeta.slug === "cn";
  const hasPeopleData = regionMeta.slug !== "global";
  const [catalogResult, indicatorsResult, refreshResult, populationResult, employmentResult, monetaryResult] =
    await Promise.allSettled([
      getIndicatorCatalog(regionMeta.apiRegion),
      getIndicators(regionMeta.apiRegion, true),
      getRefreshStatus(),
      hasPeopleData ? getPopulation(regionMeta.apiRegion) : Promise.resolve(null),
      loadRegionalEmployment(regionMeta),
      isChina ? getChinaMonetaryTransmission() : Promise.resolve(null),
    ]);

  const catalog = catalogResult.status === "fulfilled" ? catalogResult.value : [];
  const indicators = indicatorsResult.status === "fulfilled" ? indicatorsResult.value : [];
  const refresh = refreshResult.status === "fulfilled" ? refreshResult.value : null;
  const population = populationResult.status === "fulfilled" ? populationResult.value : null;
  const employment = employmentResult.status === "fulfilled" ? employmentResult.value : null;
  const monetary = monetaryResult.status === "fulfilled" ? monetaryResult.value : null;
  const requiredResults = hasPeopleData
    ? [catalogResult, indicatorsResult, refreshResult, populationResult, employmentResult, monetaryResult]
    : [catalogResult, indicatorsResult, refreshResult];
  const unavailableFeeds = requiredResults.filter((result) => result.status === "rejected").length;

  const latestByCode = new Map<string, IndicatorSummary>(
    indicators.map((indicator) => [indicator.code, indicator]),
  );
  const refreshByCode = new Map<string, RefreshResult>(
    (refresh?.results ?? []).map((result) => [result.indicator_code, result]),
  );

  const groups = new Map<string, IndicatorCatalogEntry[]>();
  for (const entry of catalog) {
    const entries = groups.get(entry.source) ?? [];
    entries.push(entry);
    groups.set(entry.source, entries);
  }
  const sourceGroups = Array.from(groups, ([key, entries]) => ({
    key,
    entries: entries.sort((left, right) => left.sort_order - right.sort_order),
    registry: SOURCE_REGISTRY_BY_KEY.get(key),
  })).sort((left, right) => {
    const kindDifference =
      KIND_ORDER[left.registry?.kind ?? "distributor"] -
      KIND_ORDER[right.registry?.kind ?? "distributor"];
    if (kindDifference !== 0) return kindDifference;
    const leftLabel = left.registry?.label ?? left.key;
    const rightLabel = right.registry?.label ?? right.key;
    return leftLabel.localeCompare(rightLabel, "zh-CN");
  });

  const policySeries = monetary?.series.find((series) => series.key === "policy_rate");
  const policyValue = policySeries?.current_value ?? POLICY_RATE_FALLBACK.value;
  const policyEffectiveDate = policySeries?.effective_date ?? POLICY_RATE_FALLBACK.effectiveDate;
  const apiPolicyVerified =
    policySeries?.verified_through ??
    monetary?.freshness?.policy_rate_verified_through;
  const apiPolicyIsCurrent =
    apiPolicyVerified !== undefined &&
    apiPolicyVerified >= POLICY_RATE_FALLBACK.verifiedThrough;
  const policyVerified = apiPolicyIsCurrent
    ? apiPolicyVerified
    : POLICY_RATE_FALLBACK.verifiedThrough;
  const policySourceUrl = apiPolicyIsCurrent
    ? policySeries?.source_url ?? POLICY_RATE_FALLBACK.sourceUrl
    : POLICY_RATE_FALLBACK.sourceUrl;
  const refreshCompleted = refresh?.finished_at ?? refresh?.started_at;
  const catalogCodes = new Set(catalog.map((entry) => entry.code));
  const regionalRefreshResults = (refresh?.results ?? []).filter((result) =>
    catalogCodes.has(result.indicator_code),
  );
  const regionalRefreshCounts = regionalRefreshResults.reduce(
    (counts, result) => {
      if (result.status === "success") counts.success += 1;
      else if (result.status === "no_change") counts.noChange += 1;
      else if (result.status === "failed") counts.failed += 1;
      return counts;
    },
    { success: 0, noChange: 0, failed: 0 },
  );
  const visibleCount = catalog.filter((entry) => entry.is_visible).length;
  const modelCount = catalog.length - visibleCount;
  const specialSeriesCount = (population?.series.length ?? 0) + (employment?.seriesCount ?? 0);

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <div className="max-w-3xl">
        <div className="flex items-center gap-2 text-sm font-medium text-zinc-600 dark:text-zinc-300">
          <Database aria-hidden="true" className="size-4" />
          {regionMeta.label} · 完整数据字典与运行水位
        </div>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">{regionMeta.label}数据来源与更新</h1>
        <p className="mt-3 text-base leading-7 text-muted-foreground">
          {regionMeta.description} 本页同时覆盖页面指标、模型底层、人口、就业和地区专题，说明每项数据从哪里来、按什么频率观察、通常滞后多久，以及系统最近一次实际检查结果。
        </p>
      </div>

      <nav aria-label="按国家或地区查看数据来源" className="no-scrollbar mt-6 overflow-x-auto">
        <div className="flex w-max gap-2">
          {DATA_SOURCE_REGIONS.map((item) => {
            const active = item.slug === regionMeta.slug;
            return (
              <Link
                key={item.slug}
                href={`/data-sources/${item.slug}`}
                aria-current={active ? "page" : undefined}
                className={`rounded-full border px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "border-zinc-700 bg-zinc-700 text-white dark:border-zinc-300 dark:bg-zinc-300 dark:text-zinc-950"
                    : "border-zinc-200 bg-zinc-50 text-zinc-600 hover:bg-zinc-100 hover:text-zinc-950 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-white"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      </nav>

      {unavailableFeeds > 0 && (
        <div className="mt-6 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
          <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <p>
            有 {unavailableFeeds} 项动态状态暂未连接；下方静态来源说明仍可查看，最新观察期和刷新结果会在后端恢复后自动出现。
          </p>
        </div>
      )}

      <section aria-labelledby="update-overview-title" className="mt-8">
        <h2 id="update-overview-title" className="text-xl font-semibold">更新概览</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Card size="sm">
            <CardHeader>
              <Layers3 aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>{catalog.length || "--"} 项常规指标</CardTitle>
              <CardDescription>{visibleCount} 项页面展示 · {modelCount} 项模型 / 证据底层</CardDescription>
            </CardHeader>
            <CardContent className="text-xs leading-5 text-muted-foreground">
              目录已按 {regionMeta.label} 单独筛选，不混入其他国家或地区的数据。
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <Clock3 aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>{formatDateTime(refreshCompleted)}</CardTitle>
              <CardDescription>最近一轮系统刷新</CardDescription>
            </CardHeader>
            <CardContent className="text-xs leading-5 text-muted-foreground">
              {refresh
                ? `本地区成功 ${regionalRefreshCounts.success} 项，无变化 ${regionalRefreshCounts.noChange} 项，失败 ${regionalRefreshCounts.failed} 项；系统每 6 小时检查。`
                : "暂未读取到刷新批次记录。"}
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <Database aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>{hasPeopleData ? `${specialSeriesCount} 项人口 / 就业序列` : "跨市场完整目录"}</CardTitle>
              <CardDescription>{hasPeopleData ? "专题接口单独纳入" : "汇率、商品与全球指标"}</CardDescription>
            </CardHeader>
            <CardContent className="text-xs leading-5 text-muted-foreground">
              {hasPeopleData
                ? `人口数据更新至 ${population?.last_updated ?? "暂未连接"}；就业月度数据至 ${employment?.latestMonth ?? "暂未连接"}。`
                : "全球页只展示不归属于单一经济体的数据，不重复各国家目录。"}
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <ShieldCheck aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>{sourceGroups.length || "--"} 类常规来源</CardTitle>
              <CardDescription>原始、分发与派生分层</CardDescription>
            </CardHeader>
            <CardContent className="text-xs leading-5 text-muted-foreground">
              第三方分发渠道会明确标注，不把“抓取渠道”误写成“原始发布机构”。
            </CardContent>
          </Card>
        </div>
      </section>

      <section aria-labelledby="time-watermarks-title" className="mt-10">
        <h2 id="time-watermarks-title" className="text-xl font-semibold">先分清四个时间</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card size="sm">
            <CardHeader>
              <CalendarClock aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>观察期</CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-6 text-muted-foreground">
              数值描述的经济时期，例如“2026 年 8 月 CPI”。它不是数据在网页上出现的日期。
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <FileClock aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>官方发布时间</CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-6 text-muted-foreground">
              发布机构首次公开该观察值的时间。回测优先用这个时间判断当时是否已知。
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <RefreshCw aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>系统取得时间</CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-6 text-muted-foreground">
              本系统实际抓取并入库的时间；网络或源站异常会让它晚于官方发布时间。
            </CardContent>
          </Card>
          <Card size="sm">
            <CardHeader>
              <ShieldCheck aria-hidden="true" className="size-4 text-zinc-500" />
              <CardTitle>源端核验日期</CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-6 text-muted-foreground">
              仅当来源明确给出“当前值核验至何日”时记录；它与本系统抓取时间分开，不能相互代替。
            </CardContent>
          </Card>
        </div>
      </section>

      <section aria-labelledby="source-groups-title" className="mt-10">
        <div className="max-w-3xl">
          <h2 id="source-groups-title" className="text-xl font-semibold">{regionMeta.label}常规指标来源</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            频率是指标的观察频率；“典型滞后”是用于建模的保守估计，不替代每一条观测的真实发布日期。
          </p>
        </div>
        {sourceGroups.length > 0 ? (
          <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {sourceGroups.map(({ key, entries, registry }) => {
              const kind = registry?.kind ?? "distributor";
              return (
                <Card key={key} size="sm">
                  <CardHeader>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <Badge variant="outline" className={KIND_STYLES[kind]}>
                        {KIND_LABELS[kind]}
                      </Badge>
                      <span className="text-xs tabular-nums text-muted-foreground">{entries.length} 项</span>
                    </div>
                    <CardTitle className="mt-2">
                      <SourceLink href={registry?.url}>{registry?.label ?? key}</SourceLink>
                    </CardTitle>
                    <CardDescription>{registry?.institution ?? key}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <p className="text-sm leading-6">
                      {registry?.description ?? "该来源已进入统一指标目录。"}
                    </p>
                    <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1.5 text-xs leading-5">
                      <dt className="text-muted-foreground">观察频率</dt>
                      <dd>{frequencySummary(entries)}</dd>
                      <dt className="text-muted-foreground">典型滞后</dt>
                      <dd>{lagRange(entries)}</dd>
                      <dt className="text-muted-foreground">取得方式</dt>
                      <dd>{registry?.access ?? "项目采集器"}</dd>
                    </dl>
                    {registry?.caveat && (
                      <p className="border-t pt-3 text-xs leading-5 text-muted-foreground">
                        {registry.caveat}
                      </p>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ) : (
          <div className="mt-4 rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            {regionMeta.label}指标目录暂不可用；来源说明会在后端恢复后自动加载。
          </div>
        )}
      </section>

      <section aria-labelledby="special-sources-title" className="mt-10">
        <h2 id="special-sources-title" className="text-xl font-semibold">
          {hasPeopleData
            ? `${regionMeta.label}人口、就业与专题来源`
            : `${regionMeta.label}专题与派生来源`}
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          {hasPeopleData
            ? "这些数据不在常规指标接口中，但同样属于本地区页面和分析的完整数据范围，因此单独列出来源与更新水位。"
            : "跨市场预测与派生结果使用上方常规指标作为输入，并与外部原始数据分开标识。"}
        </p>
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {hasPeopleData && (
            <Card size="sm">
              <CardHeader>
                <Badge variant="outline" className={KIND_STYLES.international}>人口专题</Badge>
                <CardTitle className="mt-2">
                  {population ? (
                    <SourceLink href={population.source_url}>{population.source}</SourceLink>
                  ) : (
                    "人口数据暂未连接"
                  )}
                </CardTitle>
                <CardDescription>{population?.series.length ?? 0} 组年度序列与年龄结构</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm leading-6">
                <p>人口总量、年龄结构和劳动力供给底盘通过专题接口取得，按年度或数据集版本更新。</p>
                <p className="text-xs text-muted-foreground">
                  数据集更新时间：{population?.last_updated ?? "暂未取得"}
                  {population?.pyramid_year ? ` · 年龄结构年份：${population.pyramid_year}` : ""}
                </p>
              </CardContent>
            </Card>
          )}

          {hasPeopleData && (
            <Card size="sm">
              <CardHeader>
                <Badge variant="outline" className={KIND_STYLES.official}>就业专题</Badge>
                <CardTitle className="mt-2">{regionMeta.label}就业与劳动力</CardTitle>
                <CardDescription>{employment?.seriesCount ?? 0} 组月度、季度或年度序列</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm leading-6">
                {employment?.sources.length ? (
                  <ul className="space-y-2">
                    {employment.sources.map((source) => (
                      <li key={`${source.name}-${source.url}`}>
                        <div className="font-medium"><SourceLink href={source.url}>{source.name}</SourceLink></div>
                        <div className="text-xs leading-5 text-muted-foreground">{source.description}</div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground">就业来源暂未连接。</p>
                )}
                <p className="border-t pt-2 text-xs text-muted-foreground">
                  最新月度观察：{employment?.latestMonth ?? "暂未取得"}
                  {employment?.annualUpdated ? ` · 年度数据集：${employment.annualUpdated}` : ""}
                </p>
              </CardContent>
            </Card>
          )}

          <Card size="sm">
            <CardHeader>
              <Badge variant="outline" className={KIND_STYLES.derived}>预测与分析输出</Badge>
              <CardTitle className="mt-2">基于本地区输入的模型结果</CardTitle>
              <CardDescription>不是额外的外部数据源</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm leading-6">
              <p>
                指标预测、综合分析和派生信号只使用本页披露的输入序列；常规目录中共有 {modelCount} 项模型或证据底层数据。
              </p>
              <p className="text-xs text-muted-foreground">
                派生指标披露公式与版本；统计预测会与原始历史数据分开标识。
              </p>
            </CardContent>
          </Card>

          {isChina && (
            <Card size="sm" className="border-zinc-300 ring-zinc-300 dark:border-zinc-700 dark:ring-zinc-700">
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Badge variant="outline" className={KIND_STYLES.official}>政策利率专项</Badge>
                  <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200">
                    核验至 {policyVerified}
                  </Badge>
                </div>
                <CardTitle className="mt-2">7 天期逆回购操作利率 · {policyValue.toFixed(2)}%</CardTitle>
                <CardDescription>本轮有效起始 {policyEffectiveDate}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm leading-6">
                <p><SourceLink href={policySourceUrl}>中国人民银行公开市场业务交易公告</SourceLink></p>
                <p>工作日有操作时按公告核验；零操作或未明确列示 7 天利率时沿用最近一次明确值，核验日之后不外推。</p>
              </CardContent>
            </Card>
          )}

          {isChina && (
            <Card size="sm">
              <CardHeader>
                <Badge variant="outline" className={KIND_STYLES.official}>官方市场基准</Badge>
                <CardTitle className="mt-2">
                  <SourceLink href="https://www.chinamoney.com.cn/chinese/bkfrr/">中国外汇交易中心 FDR007</SourceLink>
                </CardTitle>
                <CardDescription>交易日日度 · 月内均值</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm leading-6">
                <p>用作可稳定取得的 DR007 代理；当月值是截至最新交易日的月内均值，不是完整月均。</p>
                <p className="text-xs text-muted-foreground">
                  当前市场观察日：{monetary?.freshness?.market_observation_date ?? "暂未连接"}
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </section>

      <section aria-labelledby="catalog-title" className="mt-10">
        <div className="max-w-3xl">
          <h2 id="catalog-title" className="text-xl font-semibold">{regionMeta.label}指标明细目录</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            展开来源可查看指标级出处、频率、典型时滞、最新观察期、最近抓取结果和口径备注。隐藏项是模型底层或证据辅助序列，不等于异常。
          </p>
        </div>
        <div className="mt-4 space-y-3">
          {sourceGroups.map(({ key, entries, registry }, index) => (
            <details
              key={key}
              open={index === 0}
              className="group overflow-hidden rounded-xl border bg-card"
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 marker:hidden hover:bg-muted/50">
                <div>
                  <div className="font-medium">{registry?.label ?? key}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {entries.length} 项 · {frequencySummary(entries)} · {lagRange(entries)}
                  </div>
                </div>
                <span className="text-xs text-muted-foreground group-open:hidden">展开</span>
                <span className="hidden text-xs text-muted-foreground group-open:inline">收起</span>
              </summary>
              <div className="overflow-x-auto border-t">
                <table className="w-full min-w-[1100px] text-left text-xs">
                  <thead className="bg-muted/50 text-muted-foreground">
                    <tr>
                      <th className="px-4 py-3 font-medium">指标</th>
                      <th className="px-4 py-3 font-medium">地区 / 用途</th>
                      <th className="px-4 py-3 font-medium">频率 / 滞后</th>
                      <th className="px-4 py-3 font-medium">最新观察期</th>
                      <th className="px-4 py-3 font-medium">最近抓取 / 源端核验</th>
                      <th className="px-4 py-3 font-medium">口径与公式</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {entries.map((entry) => {
                      const latest = latestByCode.get(entry.code);
                      const run = refreshByCode.get(entry.code);
                      return (
                        <tr key={entry.code} className="align-top hover:bg-muted/30">
                          <td className="px-4 py-3">
                            <div className="font-medium text-foreground">{entry.name}</div>
                            <div className="mt-1 font-mono text-[11px] text-muted-foreground">{entry.code}</div>
                          </td>
                          <td className="px-4 py-3">
                            <div>{REGION_LABELS[entry.region] ?? entry.region}</div>
                            <div className="mt-1 text-muted-foreground">
                              {entry.is_visible ? "页面指标" : "模型 / 证据底层"}
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            <div>{FREQUENCY_LABELS[entry.frequency] ?? entry.frequency}</div>
                            <div className="mt-1 text-muted-foreground">{lagLabel(entry.release_lag_months)}</div>
                          </td>
                          <td className="px-4 py-3">
                            <div className="tabular-nums">{latest?.latest_date ?? "暂无常规值"}</div>
                            <Badge variant="outline" className={`mt-1.5 ${freshnessStyle(latest?.freshness)}`}>
                              {latest?.freshness_label ?? "未进入常规摘要"}
                            </Badge>
                          </td>
                          <td className="px-4 py-3">
                            <div>{formatDateTime(run?.last_success_at)}</div>
                            <div className="mt-1 text-muted-foreground">{refreshStatusLabel(run?.status)}</div>
                            {run?.source_verified_through && (
                              <div className="mt-1 text-muted-foreground">
                                源端核验至 {run.source_verified_through}
                              </div>
                            )}
                          </td>
                          <td className="max-w-md px-4 py-3 leading-5 text-muted-foreground">
                            {entry.formula && (
                              <div className="mb-1 text-foreground">
                                {entry.formula}
                                {entry.formula_version ? ` · v${entry.formula_version}` : ""}
                              </div>
                            )}
                            {entry.notes || "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          ))}
        </div>
      </section>

      <section aria-labelledby="audit-title" className="mt-10 border-t pt-8">
        <h2 id="audit-title" className="text-xl font-semibold">更新与审计规则</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <div className="rounded-xl bg-zinc-50 p-4 dark:bg-zinc-900">
            <CheckCircle2 aria-hidden="true" className="size-4 text-emerald-600" />
            <h3 className="mt-3 font-medium">源端无新值，不制造新数据</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              定时任务只检查并写入可验证的新观测；无变化会记录为“源端无新值”。
            </p>
          </div>
          <div className="rounded-xl bg-zinc-50 p-4 dark:bg-zinc-900">
            <Layers3 aria-hidden="true" className="size-4 text-sky-600" />
            <h3 className="mt-3 font-medium">修订值保留版本</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              当前序列用于展示，历史版本与首次可用时间用于回测，避免把后来修订的信息带回过去。
            </p>
          </div>
          <div className="rounded-xl bg-zinc-50 p-4 dark:bg-zinc-900">
            <ShieldCheck aria-hidden="true" className="size-4 text-violet-600" />
            <h3 className="mt-3 font-medium">派生值披露公式</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              项目计算的派生指标带公式版本和输入代码；预测与分析结果不会冒充官方发布值。
            </p>
          </div>
        </div>
        <p className="mt-5 text-xs leading-5 text-muted-foreground">
          数据新鲜度依据序列自身历史间隔和当前日期计算，只用于发现明显滞后；它不是对发布机构是否按时发布的评价。
          {isChina && " 中国政策利率当前为人工维护日程；官方 HTML 虽可读取，但在自动解析、字段校验和失败告警接入前仍按人工核验标记。"}
        </p>
      </section>
    </main>
  );
}
