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
    <nav className="w-40 shrink-0 border-r border-border px-3 py-10">
      <div className="px-3 text-sm font-semibold">经济学看板</div>
      <ul className="mt-6 space-y-1">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`block rounded-md px-3 py-2 text-sm transition-colors ${
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
