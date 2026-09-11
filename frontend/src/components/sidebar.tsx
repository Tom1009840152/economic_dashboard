"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "综合" },
  { href: "/country/cn", label: "中国" },
  { href: "/country/us", label: "美国" },
  { href: "/country/jp", label: "日本" },
  { href: "/country/eu", label: "欧盟" },
  { href: "/country/kr", label: "韩国" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="地区导航"
      className="sticky top-0 z-40 flex w-full shrink-0 items-center gap-3 border-b border-border bg-background px-4 py-3 sm:static sm:block sm:w-40 sm:border-r sm:border-b-0 sm:px-3 sm:py-10"
    >
      <div className="shrink-0 whitespace-nowrap text-sm font-semibold sm:px-3">经济学看板</div>
      <ul className="no-scrollbar flex min-w-0 flex-1 gap-1 overflow-x-auto sm:mt-6 sm:block sm:space-y-1">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href} className="shrink-0">
              <Link
                href={item.href}
                className={`block whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
