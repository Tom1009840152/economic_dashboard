import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getEmployment,
  getIndicators,
  getInternationalEmployment,
  getPopulation,
  type EmploymentDashboard,
  type IndicatorSummary,
  type InternationalEmploymentDashboard,
  type PopulationDashboard,
} from "@/lib/api";
import { IndicatorCard } from "@/components/indicator-card";
import { BondSummaryCard } from "@/components/bond-summary-card";
import { MoneySupplySummaryCard } from "@/components/money-supply-summary-card";
import { FadeIn } from "@/components/fade-in";
import { COMPANION_CODES } from "@/lib/companion-indicators";
import { PopulationSummaryCard } from "@/components/population-summary-card";
import { EmploymentSummaryCard } from "@/components/employment-summary-card";
import { USEmploymentSummaryCard } from "@/components/us-employment-summary-card";
import { InternationalEmploymentSummaryCard } from "@/components/international-employment-summary-card";
import {
  Activity,
  Banknote,
  BriefcaseBusiness,
  ChartCandlestick,
  Gauge,
  type LucideIcon,
} from "lucide-react";

export const dynamic = "force-dynamic";

const REGION_META: Record<string, { code: string; label: string }> = {
  cn: { code: "CN", label: "中国" },
  us: { code: "US", label: "美国" },
  jp: { code: "JP", label: "日本" },
  eu: { code: "EU", label: "欧盟" },
  kr: { code: "KR", label: "韩国" },
};

type CardItem =
  | { kind: "indicator"; indicator: IndicatorSummary }
  | { kind: "bond"; bonds: IndicatorSummary[] }
  | { kind: "money"; indicators: IndicatorSummary[] }
  | { kind: "population"; data: PopulationDashboard }
  | { kind: "employment"; data: EmploymentDashboard }
  | { kind: "international-employment"; data: InternationalEmploymentDashboard };

type GroupId = "cycle" | "prices" | "people" | "liquidity" | "markets";

const GROUPS: Array<{
  id: GroupId;
  title: string;
  description: string;
  icon: LucideIcon;
}> = [
  {
    id: "cycle",
    title: "增长与景气",
    description: "经济增速、生产活动、需求动能与领先信号",
    icon: Activity,
  },
  {
    id: "prices",
    title: "物价与通胀",
    description: "居民端、生产端及核心价格压力",
    icon: Gauge,
  },
  {
    id: "people",
    title: "人口与就业",
    description: "长期人口底盘、劳动力供给与就业质量",
    icon: BriefcaseBusiness,
  },
  {
    id: "liquidity",
    title: "货币与利率",
    description: "信用扩张、流动性、政策利率与收益率曲线",
    icon: Banknote,
  },
  {
    id: "markets",
    title: "市场与外部",
    description: "资产价格、汇率、出口与外部金融条件",
    icon: ChartCandlestick,
  },
];

const PEOPLE_INDICATORS = new Set(["US_NFP"]);
const LIQUIDITY_INDICATORS = new Set([
  "CN_TSF",
  "US_FFR",
  "JP_BOJ",
  "JP_BOJ_ASSETS",
  "EU_ECB",
  "EU_ECB_ASSETS",
]);

function indicatorGroup(indicator: IndicatorSummary): GroupId {
  const { category, code } = indicator;

  if (PEOPLE_INDICATORS.has(code)) return "people";
  if (LIQUIDITY_INDICATORS.has(code)) return "liquidity";
  if (/_(?:CORE_)?CPI$|_PPI$|_HOG$/.test(code)) return "prices";
  if (
    category === "index" ||
    category === "forex" ||
    category === "commodity" ||
    code.endsWith("_EXPORTS") ||
    code.endsWith("_ENERGY") ||
    code.endsWith("_RESERVES")
  ) {
    return "markets";
  }
  return "cycle";
}

function cardKey(card: CardItem): string {
  if (card.kind === "indicator") return card.indicator.code;
  if (card.kind === "population") return "population";
  if (card.kind === "employment" || card.kind === "international-employment") return "employment";
  return card.kind;
}

function CountryCard({ card, region }: { card: CardItem; region: string }) {
  if (card.kind === "population") return <PopulationSummaryCard data={card.data} />;
  if (card.kind === "employment") return <EmploymentSummaryCard data={card.data} />;
  if (card.kind === "international-employment") {
    return card.data.region === "US" ? (
      <USEmploymentSummaryCard data={card.data} />
    ) : (
      <InternationalEmploymentSummaryCard data={card.data} />
    );
  }
  if (card.kind === "bond") return <BondSummaryCard region={region} bonds={card.bonds} />;
  if (card.kind === "money") {
    return <MoneySupplySummaryCard region={region} indicators={card.indicators} />;
  }
  return <IndicatorCard indicator={card.indicator} showCategory={false} />;
}

export default async function CountryPage(props: PageProps<"/country/[region]">) {
  const { region } = await props.params;
  const meta = REGION_META[region];
  if (!meta) notFound();

  const [indicators, population, employment, internationalEmployment] = await Promise.all([
    getIndicators(meta.code),
    getPopulation(meta.code).catch(() => null),
    region === "cn" ? getEmployment("CN").catch(() => null) : Promise.resolve(null),
    region !== "cn" ? getInternationalEmployment(meta.code).catch(() => null) : Promise.resolve(null),
  ]);

  // 国债收益率、货币供给这两类，一个国家会有好几个细分指标，不逐个铺卡片，
  // 各自合并成一张汇总卡，点进去再切换具体看哪个（国债看期限，货币供给看M0/M1/M2/口径）
  const cards: CardItem[] = [];
  const bondCard: { kind: "bond"; bonds: IndicatorSummary[] } = { kind: "bond", bonds: [] };
  const moneyCard: { kind: "money"; indicators: IndicatorSummary[] } = { kind: "money", indicators: [] };
  let bondCardInserted = false;
  let moneyCardInserted = false;
  for (const indicator of indicators) {
    if (COMPANION_CODES.has(indicator.code)) continue;
    if (indicator.category === "bond") {
      if (!indicator.code.endsWith("10Y2Y")) {
        bondCard.bonds.push(indicator);
      }
      if (!bondCardInserted) {
        cards.push(bondCard);
        bondCardInserted = true;
      }
      continue;
    }
    if (indicator.category === "money") {
      moneyCard.indicators.push(indicator);
      if (!moneyCardInserted) {
        cards.push(moneyCard);
        moneyCardInserted = true;
      }
      continue;
    }
    cards.push({ kind: "indicator", indicator });
  }

  const groupedCards: Record<GroupId, CardItem[]> = {
    cycle: [],
    prices: [],
    people: [],
    liquidity: [],
    markets: [],
  };

  if (population) groupedCards.people.push({ kind: "population", data: population });
  if (employment) groupedCards.people.push({ kind: "employment", data: employment });
  if (internationalEmployment) {
    groupedCards.people.push({ kind: "international-employment", data: internationalEmployment });
  }

  for (const card of cards) {
    if (card.kind === "bond" || card.kind === "money") {
      groupedCards.liquidity.push(card);
    } else if (card.kind === "indicator") {
      groupedCards[indicatorGroup(card.indicator)].push(card);
    }
  }

  const sections = GROUPS.map((group) => ({
    ...group,
    cards: groupedCards[group.id],
  })).filter((group) => group.cards.length > 0);

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <div className="flex flex-col gap-2 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 text-xs font-medium tracking-[0.18em] text-muted-foreground">
            国家经济监测
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">{meta.label}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            从增长、通胀、就业、流动性与市场五个维度观察经济
          </p>
        </div>

        <div className="text-xs text-muted-foreground">
          共 {sections.reduce((total, section) => total + section.cards.length, 0)} 个观察入口
        </div>
      </div>

      {region === "jp" && (
        <Link
          href="/topics/cny-jpy"
          className="mt-4 inline-flex items-center rounded-full border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          查看人民币兑日元专题 →
        </Link>
      )}

      {sections.length > 0 && (
        <nav
          aria-label="指标分类快速定位"
          className="no-scrollbar sticky top-[68px] z-20 mt-6 flex gap-2 overflow-x-auto rounded-2xl border bg-background/90 p-2 shadow-sm backdrop-blur sm:top-3"
        >
          {sections.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              className="inline-flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <section.icon className="size-4" aria-hidden="true" />
              <span>{section.title}</span>
              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[11px] tabular-nums">
                {section.cards.length}
              </span>
            </a>
          ))}
        </nav>
      )}

      <div className="space-y-12 pb-8">
        {sections.map((section, sectionIndex) => (
          <section key={section.id} id={section.id} className="scroll-mt-24 pt-10">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 rounded-xl bg-muted p-2.5 text-foreground">
                  <section.icon className="size-5" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold tracking-tight">{section.title}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{section.description}</p>
                </div>
              </div>
              <span className="hidden pt-1 text-xs text-muted-foreground sm:block">
                {section.cards.length} 项
              </span>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {section.cards.map((card, cardIndex) => (
                <FadeIn
                  key={cardKey(card)}
                  delay={Math.min((sectionIndex * 2 + cardIndex) * 0.035, 0.28)}
                >
                  <CountryCard card={card} region={meta.code} />
                </FadeIn>
              ))}
            </div>
          </section>
        ))}
      </div>

      {indicators.length === 0 && (
        <p className="mt-10 text-sm text-muted-foreground">还没有该地区的数据。</p>
      )}
    </main>
  );
}
