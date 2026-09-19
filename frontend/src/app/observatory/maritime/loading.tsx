import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function MaritimeObservatoryLoading() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <div>
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-8 w-72 max-w-full" />
        <Skeleton className="mt-3 h-4 w-full max-w-2xl" />
      </div>

      <Card className="mt-8 overflow-hidden">
        <CardHeader className="bg-slate-950 py-7">
          <div className="text-sm font-medium text-slate-200">正在汇总最新港口与航道记录…</div>
          <p className="text-xs leading-5 text-slate-400">
            全球流量、中国装船与关键咽喉会分开计算，不用价格填补缺失数量。
          </p>
        </CardHeader>
        <CardContent className="p-5">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-40 w-full" />
            ))}
          </div>
          <div className="mt-5 grid gap-5 xl:grid-cols-[1.55fr_0.85fr]">
            <Skeleton className="h-80 w-full" />
            <Skeleton className="h-80 w-full" />
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
