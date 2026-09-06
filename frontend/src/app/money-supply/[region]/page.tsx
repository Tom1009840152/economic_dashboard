import Link from "next/link";
import { notFound } from "next/navigation";
import { getIndicatorForecast, getIndicatorHistory } from "@/lib/api";
import { MoneySupplyDetail } from "@/components/money-supply-detail";
import type { ForecastPoint } from "@/lib/api";

export const dynamic = "force-dynamic";

interface RegionMeta {
  label: string;
  codePrefix: string;
  groups: { key: string; label: string }[];
  initialGroup: string;
  spreadCode?: string;
  spreadName?: string;
  description: string;
}

const REGION_META: Record<string, RegionMeta> = {
  cn: {
    label: "中国",
    codePrefix: "CN",
    groups: [
      { key: "M0", label: "M0（流通中现金）" },
      { key: "M1", label: "M1（狭义货币）" },
      { key: "M2", label: "M2（广义货币）" },
    ],
    initialGroup: "M2",
    spreadCode: "CN_M1M2",
    spreadName: "M1-M2剪刀差",
    description: "切换 M0/M1/M2，每个都能看绝对值、同比、环比；M1-M2剪刀差反映企业资金活化程度",
  },
  us: {
    label: "美国",
    codePrefix: "US",
    groups: [
      { key: "BASE", label: "货币基础（顶替M0）" },
      { key: "M1", label: "M1" },
      { key: "M2", label: "M2" },
    ],
    initialGroup: "M2",
    description:
      "数据来自美联储官方FRED（不是akshare）；美国没有官方M0概念，用货币基础顶替，没有对应中国剪刀差的惯用指标",
  },
};

export default async function MoneySupplyPage(props: PageProps<"/money-supply/[region]">) {
  const { region } = await props.params;
  const meta = REGION_META[region];
  if (!meta) notFound();

  const initialCode = `${meta.codePrefix}_${meta.initialGroup}_ABS`;
  const history = await getIndicatorHistory(initialCode);
  let forecast: ForecastPoint[] = [];
  try {
    forecast = (await getIndicatorForecast(initialCode)).forecast;
  } catch {
    // 历史数据不够长时后端会拒绝预测，图表仍然只展示历史走势
  }

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <Link href={`/country/${region}`} className="text-sm text-muted-foreground hover:underline">
        ← 返回{meta.label}
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">{meta.label}货币供给</h1>
      <p className="mt-1 text-sm text-muted-foreground">{meta.description}</p>

      <div className="mt-8">
        <MoneySupplyDetail
          codePrefix={meta.codePrefix}
          groups={meta.groups}
          spreadCode={meta.spreadCode}
          spreadName={meta.spreadName}
          initialGroup={meta.initialGroup}
          initialView="ABS"
          initialData={{
            name: history.name,
            unit: history.unit,
            points: history.points,
            forecast,
          }}
        />
      </div>
    </main>
  );
}
