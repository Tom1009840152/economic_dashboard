import Link from "next/link";
import { notFound } from "next/navigation";
import { getIndicatorHistory } from "@/lib/api";
import { BondYieldDetail } from "@/components/bond-yield-detail";

export const dynamic = "force-dynamic";

const REGION_BONDS: Record<
  string,
  { label: string; maturities: { code: string; label: string }[]; spread: { code: string; label: string } }
> = {
  cn: {
    label: "中国",
    maturities: [
      { code: "CN_2Y", label: "2年" },
      { code: "CN_5Y", label: "5年" },
      { code: "CN_10Y", label: "10年" },
      { code: "CN_30Y", label: "30年" },
    ],
    spread: { code: "CN_10Y2Y", label: "中国10年-2年国债利差" },
  },
  us: {
    label: "美国",
    maturities: [
      { code: "US_2Y", label: "2年" },
      { code: "US_5Y", label: "5年" },
      { code: "US_10Y", label: "10年" },
      { code: "US_30Y", label: "30年" },
    ],
    spread: { code: "US_10Y2Y", label: "美国10年-2年国债利差" },
  },
};

export default async function BondsPage(props: PageProps<"/bonds/[region]">) {
  const { region } = await props.params;
  const meta = REGION_BONDS[region];
  if (!meta) notFound();

  const [maturities, spreadHistory] = await Promise.all([
    Promise.all(
      meta.maturities.map(async (m) => ({
        key: m.code,
        label: m.label,
        points: (await getIndicatorHistory(m.code)).points,
      }))
    ),
    getIndicatorHistory(meta.spread.code),
  ]);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <Link href={`/country/${region}`} className="text-sm text-muted-foreground hover:underline">
        ← 返回{meta.label}
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">{meta.label}国债收益率</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        2年/5年/10年/30年期收益率一起看，下方是10年-2年利差——历史上倒挂（转负）常被视为衰退预警信号
      </p>

      <div className="mt-8">
        <BondYieldDetail
          maturities={maturities}
          spread={{ key: meta.spread.code, label: meta.spread.label, points: spreadHistory.points }}
        />
      </div>
    </main>
  );
}
