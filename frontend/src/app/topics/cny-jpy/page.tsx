import Link from "next/link";
import { CnyJpyTopic } from "@/components/cny-jpy-topic";
import { getIndicatorHistory } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function CnyJpyTopicPage() {
  const [jpyCnyHistory, usdCnyHistory] = await Promise.all([
    getIndicatorHistory("JPYCNY"),
    getIndicatorHistory("USDCNY"),
  ]);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">← 返回看板</Link>
      <h1 className="mt-3 text-2xl font-semibold">人民币兑日元专题</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        以 CNY/JPY 为统一口径，分辨交叉汇率变动来自人民币还是日元。
      </p>
      <div className="mt-8">
        <CnyJpyTopic jpyCnyHistory={jpyCnyHistory.points} usdCnyHistory={usdCnyHistory.points} />
      </div>
    </main>
  );
}
