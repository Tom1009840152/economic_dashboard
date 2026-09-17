import Link from "next/link";
import { ArrowRight, GitBranch, Landmark, Waves } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  MonetaryTransmissionDashboard,
  MonetaryTransmissionSignal,
} from "@/lib/api";

function signalByKey(
  data: MonetaryTransmissionDashboard,
  key: string,
): MonetaryTransmissionSignal | undefined {
  return data.signals.find((signal) => signal.key === key);
}

function formatted(signal: MonetaryTransmissionSignal | undefined): string {
  if (!signal) return "--";
  const sign = signal.value > 0 ? "+" : "";
  return `${sign}${signal.value.toFixed(signal.unit === "bp" ? 1 : 2)} ${signal.unit}`;
}

const TONE_STYLES = {
  positive: "border-emerald-200 bg-emerald-50/70 text-emerald-800",
  neutral: "border-blue-200 bg-blue-50/70 text-blue-800",
  caution: "border-amber-200 bg-amber-50/70 text-amber-900",
};

export function MonetaryTransmissionSummaryCard({
  data,
}: {
  data: MonetaryTransmissionDashboard;
}) {
  const realRate = signalByKey(data, "real_policy_rate");
  const liquidity = signalByKey(data, "liquidity_gap");
  const credit = signalByKey(data, "credit_impulse");

  return (
    <Link
      href="/analysis/cn/monetary-transmission"
      className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      aria-label="查看中国货币政策与信用传导详情"
    >
      <Card className="group overflow-hidden border-foreground/15 bg-gradient-to-br from-background via-background to-muted/55 transition-all hover:border-foreground/30 hover:shadow-md">
        <CardHeader className="gap-4 pb-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium tracking-[0.16em] text-muted-foreground">
              <GitBranch className="size-4" aria-hidden="true" />
              政策传导链
            </div>
            <CardTitle className="text-xl">{data.title}</CardTitle>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              {data.summary}
            </p>
          </div>
          <Badge variant="outline" className={TONE_STYLES[data.tone]}>
            {data.status}
          </Badge>
        </CardHeader>

        <CardContent>
          <div className="grid gap-3 sm:grid-cols-3">
            {[
              { signal: realRate, icon: Landmark, label: "价格约束" },
              { signal: liquidity, icon: Waves, label: "资金面" },
              { signal: credit, icon: GitBranch, label: "实体传导" },
            ].map(({ signal, icon: Icon, label }) => (
              <div key={label} className="rounded-xl border bg-background/75 p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">{label}</span>
                  <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                </div>
                <div className="mt-2 text-2xl font-semibold tabular-nums">
                  {formatted(signal)}
                </div>
                <div className="mt-1 flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">{signal?.name ?? "--"}</span>
                  <span className="font-medium">{signal?.state ?? "--"}</span>
                </div>
                <div className="mt-2 text-[11px] text-muted-foreground">
                  观察期 {signal?.period ?? "--"}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-4 flex flex-col gap-2 border-t pt-4 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <span>
              市场观察 {data.freshness?.market_observation_date ?? data.as_of} · 政策核验 {data.freshness?.policy_rate_verified_through ?? "--"} · 信用观察 {data.freshness?.credit_observation_period ?? credit?.period ?? "--"}
            </span>
            <span className="inline-flex items-center gap-1 font-medium text-foreground">
              展开分析与等式
              <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
