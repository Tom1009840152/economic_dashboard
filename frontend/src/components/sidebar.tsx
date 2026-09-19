"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ChevronDown, Database, Ship } from "lucide-react";
import {
  COUNTRY_REGIONS,
  countrySectionForPath,
  defaultSectionForRegion,
  sectionsForRegion,
  type CountryRegion,
} from "@/lib/country-sections";
import { DATA_SOURCE_REGION_BY_SLUG, type DataSourceRegion } from "@/lib/data-source-registry";

export function Sidebar() {
  const pathname = usePathname();
  const sourceRegionMatch = pathname.match(/^\/data-sources\/(cn|us|jp|eu|uk|kr|global)$/);
  const sourceRegion = sourceRegionMatch?.[1] as DataSourceRegion | undefined;
  const sourcesActive = sourceRegion !== undefined || pathname === "/data-sources";
  const maritimeActive = pathname === "/observatory/maritime";
  const activeContext = countrySectionForPath(pathname);
  const sourceHrefRegion = sourceRegion
    ?? activeContext?.region
    ?? (pathname === "/" ? "global" : "cn");
  const sourceHref = DATA_SOURCE_REGION_BY_SLUG.has(sourceHrefRegion)
    ? `/data-sources/${sourceHrefRegion}`
    : "/data-sources/cn";
  const [expansion, setExpansion] = useState<{
    pathname: string;
    region: CountryRegion | null;
  }>({
    pathname,
    region: activeContext?.region ?? null,
  });
  const expandedRegion = expansion.pathname === pathname
    ? expansion.region
    : activeContext?.region ?? null;

  const renderSubmenu = (region: CountryRegion, horizontal = false) => (
    <ul className={horizontal ? "flex gap-1" : "space-y-0.5"}>
      {sectionsForRegion(region).map((section) => {
        const active = activeContext?.region === region && activeContext.section === section.slug;
        return (
          <li key={section.slug}>
            <Link
              href={`/country/${region}/${section.slug}`}
              className={`block whitespace-nowrap rounded-md px-3 py-1.5 text-[13px] transition-colors ${
                active
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              {section.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );

  return (
    <nav
      aria-label="主导航"
      className="sticky top-0 z-40 flex w-full shrink-0 flex-col gap-2 border-b border-border bg-background px-4 py-3 sm:h-screen sm:w-56 sm:self-start sm:overflow-y-auto sm:border-r sm:border-b-0 sm:px-3 sm:py-10"
    >
      <div className="flex min-w-0 items-center gap-3 sm:block">
        <div className="shrink-0 whitespace-nowrap text-sm font-semibold sm:px-3">经济学看板</div>
        <ul className="no-scrollbar flex min-w-0 flex-1 gap-1 overflow-x-auto sm:mt-6 sm:block sm:space-y-1">
          <li className="shrink-0">
            <Link
              href="/"
              className={`block whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${
                pathname === "/"
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
              onClick={() => setExpansion({ pathname, region: null })}
            >
              综合
            </Link>
          </li>

          {COUNTRY_REGIONS.map(({ region, label }) => {
            const active = activeContext?.region === region;
            const expanded = expandedRegion === region;
            const defaultSection = defaultSectionForRegion(region);
            return (
              <li key={region} className="shrink-0">
                <div className={`flex items-center rounded-md ${active ? "bg-muted" : ""}`}>
                  <Link
                    href={`/country/${region}/${defaultSection}`}
                    className={`min-w-0 flex-1 whitespace-nowrap rounded-l-md px-3 py-2 text-sm transition-colors ${
                      active ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
                    }`}
                    onClick={() => setExpansion({ pathname, region })}
                  >
                    {label}
                  </Link>
                  <button
                    type="button"
                    aria-label={expanded ? `收起${label}栏目` : `展开${label}栏目`}
                    aria-expanded={expanded}
                    onClick={() => setExpansion({ pathname, region: expanded ? null : region })}
                    className="rounded-r-md p-2.5 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <ChevronDown className={`size-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} />
                  </button>
                </div>
                {expanded && (
                  <div className="mt-1 ml-3 hidden border-l pl-3 sm:block">
                    {renderSubmenu(region)}
                  </div>
                )}
              </li>
            );
          })}

          <li className="shrink-0 sm:mt-4 sm:border-t sm:border-zinc-200 sm:pt-4 dark:sm:border-zinc-800">
            <Link
              href="/observatory/maritime"
              aria-current={maritimeActive ? "page" : undefined}
              className={`flex items-center gap-2 whitespace-nowrap rounded-md border px-3 py-2 text-sm transition-colors ${
                maritimeActive
                  ? "border-sky-800 bg-sky-800 text-white shadow-sm dark:border-sky-300 dark:bg-sky-300 dark:text-sky-950"
                  : "border-sky-200 bg-sky-50 text-sky-800 hover:border-sky-300 hover:bg-sky-100 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-200 dark:hover:bg-sky-900/70"
              }`}
              onClick={() => setExpansion({ pathname, region: null })}
            >
              <Ship aria-hidden="true" className="size-3.5" />
              全球海运观察
            </Link>
          </li>

          <li className="shrink-0 sm:mt-1">
            <Link
              href={sourceHref}
              aria-current={sourcesActive ? "page" : undefined}
              className={`flex items-center gap-2 whitespace-nowrap rounded-md border px-3 py-2 text-sm transition-colors ${
                sourcesActive
                  ? "border-zinc-700 bg-zinc-700 text-white shadow-sm dark:border-zinc-300 dark:bg-zinc-300 dark:text-zinc-950"
                  : "border-zinc-200 bg-zinc-100 text-zinc-700 hover:border-zinc-300 hover:bg-zinc-200 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
              }`}
              onClick={() => setExpansion({ pathname, region: null })}
            >
              <Database aria-hidden="true" className="size-3.5" />
              数据来源与更新
            </Link>
          </li>
        </ul>
      </div>

      {expandedRegion && (
        <div className="no-scrollbar w-full overflow-x-auto border-t pt-2 sm:hidden">
          <div className="w-max">{renderSubmenu(expandedRegion, true)}</div>
        </div>
      )}
    </nav>
  );
}
