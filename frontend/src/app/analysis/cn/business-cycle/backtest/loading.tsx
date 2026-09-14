import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function ChinaBusinessCycleBacktestLoading() {
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 sm:px-8 sm:py-10">
      <div className="text-sm text-muted-foreground">← 返回经济周期定位</div>
      <div className="mt-5">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="mt-3 h-4 w-full max-w-2xl" />
      </div>
      <Card className="mt-8">
        <CardHeader>
          <div className="text-sm font-medium">正在还原历史决策现场…</div>
          <p className="text-xs leading-5 text-muted-foreground">严格回测会逐月筛选当时已经发布的历史版本，通常需要十几秒。</p>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-24 w-full" />)}
          </div>
          <Skeleton className="mt-5 h-36 w-full" />
        </CardContent>
      </Card>
    </main>
  );
}
