"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function ChinaAnalysisError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("China analysis route failed", error);
  }, [error]);

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 items-center px-5 py-12 sm:px-8">
      <Card className="w-full border-amber-200 dark:border-amber-900">
        <CardHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-full bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <AlertTriangle className="size-5" aria-hidden="true" />
          </div>
          <CardTitle>中国经济分析暂时无法加载</CardTitle>
          <CardDescription className="leading-6">
            这表示本次请求失败，不表示经济信号为空或数值为零。你可以重试，或返回中国分析总览查看其余已加载模块。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button onClick={reset}>
            <RefreshCw className="size-4" aria-hidden="true" />
            重新加载
          </Button>
          <Link
            href="/country/cn/analysis"
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border px-2.5 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <ArrowLeft className="size-4" aria-hidden="true" />
            返回分析总览
          </Link>
        </CardContent>
      </Card>
    </main>
  );
}
