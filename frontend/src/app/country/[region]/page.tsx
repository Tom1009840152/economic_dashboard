import Link from "next/link";
import { notFound } from "next/navigation";
import { getIndicators, type IndicatorSummary } from "@/lib/api";
import { IndicatorCard } from "@/components/indicator-card";
import { BondSummaryCard } from "@/components/bond-summary-card";
import { FadeIn } from "@/components/fade-in";

export const dynamic = "force-dynamic";

const REGION_META: Record<string, { code: string; label: string }> = {
  cn: { code: "CN", label: "中国" },
  us: { code: "US", label: "美国" },
  jp: { code: "JP", label: "日本" },
  eu: { code: "EU", label: "欧盟" },
};

type CardItem =
  | { kind: "indicator"; indicator: IndicatorSummary }
  | { kind: "bond"; bonds: IndicatorSummary[] };

export default async function CountryPage(props: PageProps<"/country/[region]">) {
  const { region } = await props.params;
  const meta = REGION_META[region];
  if (!meta) notFound();

  const indicators = await getIndicators(meta.code);

  // 国债收益率一个国家有好几个期限，不逐个铺卡片，合并成一张卡，
  // 点进去用多线图一起看（10年-2年利差是衰退先行信号，值得放在一起对比）
  const cards: CardItem[] = [];
  const bondCard: { kind: "bond"; bonds: IndicatorSummary[] } = { kind: "bond", bonds: [] };
  let bondCardInserted = false;
  for (const indicator of indicators) {
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
        {cards.map((card, i) =>
          card.kind === "bond" ? (
            <FadeIn key="bond" delay={i * 0.05}>
              <BondSummaryCard region={meta.code} bonds={card.bonds} />
            </FadeIn>
          ) : (
            <FadeIn key={card.indicator.code} delay={i * 0.05}>
              <IndicatorCard indicator={card.indicator} />
            </FadeIn>
          )
        )}
      </div>

      {indicators.length === 0 && (
        <p className="mt-10 text-sm text-muted-foreground">还没有该地区的数据。</p>
      )}
    </main>
  );
}
