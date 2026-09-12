import { getIndicators, type IndicatorSummary } from "@/lib/api";
import { IndicatorCard } from "@/components/indicator-card";
import { ForexSummaryCard } from "@/components/forex-summary-card";
import { FadeIn } from "@/components/fade-in";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export const dynamic = "force-dynamic";

type CardItem = { kind: "indicator"; indicator: IndicatorSummary } | { kind: "forex" };

export default async function DashboardPage() {
  // 已经归到具体国家/地区的指标只在对应页展示，这里只放没有归属的
  // 全球性指标（除美元/日元/欧元外的其它汇率、黄金、原油），避免和国家页重复
  const indicators = await getIndicators("GLOBAL");
  const forex = indicators.filter((i) => i.category === "forex");

  // 指标列表已经按后端 sort_order 排好（index 在前、commodity 在后），
  // 汇率不再逐个货币铺卡片，合并成一张卡放在原来汇率组的位置，点进去再自由切换货币
  const cards: CardItem[] = [];
  let forexCardInserted = false;
  for (const indicator of indicators) {
    if (indicator.category === "forex") {
      if (!forexCardInserted) {
        cards.push({ kind: "forex" });
        forexCardInserted = true;
      }
      continue;
    }
    cards.push({ kind: "indicator", indicator });
  }

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">经济学看板</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        全球性指标一览，各主要经济体的指标见左侧对应国家或地区页
      </p>

      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <FadeIn delay={0}>
          <Link href="/topics/cny-jpy">
            <Card className="h-full transition-colors hover:border-foreground/30">
              <CardHeader>
                <CardTitle className="text-base">人民币兑日元专题</CardTitle>
                <Badge variant="secondary" className="mt-1 w-fit">CNY/JPY</Badge>
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">
                汇率走势、交叉汇率拆解与人民币／日元两侧驱动
              </CardContent>
            </Card>
          </Link>
        </FadeIn>
        {cards.map((card, i) =>
          card.kind === "forex" ? (
            <FadeIn key="forex" delay={(i + 1) * 0.05}>
              <ForexSummaryCard currencies={forex} />
            </FadeIn>
          ) : (
            <FadeIn key={card.indicator.code} delay={(i + 1) * 0.05}>
              <IndicatorCard indicator={card.indicator} />
            </FadeIn>
          )
        )}
      </div>

      {indicators.length === 0 && (
        <p className="mt-10 text-sm text-muted-foreground">
          还没有数据，等后端第一次刷新完成后刷新本页。
        </p>
      )}
    </main>
  );
}
