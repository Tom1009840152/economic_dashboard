import Link from "next/link";
import { notFound } from "next/navigation";
import { getIndicators, type IndicatorSummary } from "@/lib/api";
import { IndicatorCard } from "@/components/indicator-card";
import { BondSummaryCard } from "@/components/bond-summary-card";
import { MoneySupplySummaryCard } from "@/components/money-supply-summary-card";
import { FadeIn } from "@/components/fade-in";
import { COMPANION_CODES } from "@/lib/companion-indicators";

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
  | { kind: "money"; indicators: IndicatorSummary[] };

export default async function CountryPage(props: PageProps<"/country/[region]">) {
  const { region } = await props.params;
  const meta = REGION_META[region];
  if (!meta) notFound();

  const indicators = await getIndicators(meta.code);

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

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">{meta.label}</h1>
      <p className="mt-1 text-sm text-muted-foreground">{meta.label}相关的经济与金融指标</p>

      {region === "jp" && (
        <Link
          href="/topics/cny-jpy"
          className="mt-3 inline-block text-sm text-muted-foreground hover:underline"
        >
          查看人民币兑日元专题 →
        </Link>
      )}

      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card, i) => {
          if (card.kind === "bond") {
            return (
              <FadeIn key="bond" delay={i * 0.05}>
                <BondSummaryCard region={meta.code} bonds={card.bonds} />
              </FadeIn>
            );
          }
          if (card.kind === "money") {
            return (
              <FadeIn key="money" delay={i * 0.05}>
                <MoneySupplySummaryCard region={meta.code} indicators={card.indicators} />
              </FadeIn>
            );
          }
          return (
            <FadeIn key={card.indicator.code} delay={i * 0.05}>
              <IndicatorCard indicator={card.indicator} />
            </FadeIn>
          );
        })}
      </div>

      {indicators.length === 0 && (
        <p className="mt-10 text-sm text-muted-foreground">还没有该地区的数据。</p>
      )}
    </main>
  );
}
