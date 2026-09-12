"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  COUNTRY_REGIONS,
  countrySectionForPath,
  defaultSectionForRegion,
  sectionsForRegion,
  type CountryRegion,
} from "@/lib/country-sections";

export function Sidebar() {
  const pathname = usePathname();
  const activeContext = countrySectionForPath(pathname);
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
      aria-label="地区导航"
      className="sticky top-0 z-40 flex w-full shrink-0 flex-col gap-2 border-b border-border bg-background px-4 py-3 sm:static sm:block sm:w-56 sm:border-r sm:border-b-0 sm:px-3 sm:py-10"
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
