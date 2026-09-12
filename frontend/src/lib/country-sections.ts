export type CountrySectionSlug =
  | "analysis"
  | "growth"
  | "prices"
  | "people"
  | "monetary"
  | "markets";

export type CountryRegion = "cn" | "us" | "jp" | "eu" | "uk" | "kr";

export const COUNTRY_REGIONS: Array<{ region: CountryRegion; label: string }> = [
  { region: "cn", label: "中国" },
  { region: "us", label: "美国" },
  { region: "jp", label: "日本" },
  { region: "eu", label: "欧元区" },
  { region: "uk", label: "英国" },
  { region: "kr", label: "韩国" },
];

export const COUNTRY_SECTIONS: Array<{
  slug: CountrySectionSlug;
  label: string;
  description: string;
}> = [
  {
    slug: "analysis",
    label: "综合分析",
    description: "把分散指标放进传导链中，先看结论，再追溯证据与口径",
  },
  {
    slug: "growth",
    label: "增长与景气",
    description: "经济增速、生产活动、需求动能与领先信号",
  },
  {
    slug: "prices",
    label: "物价与通胀",
    description: "居民端、生产端及核心价格压力",
  },
  {
    slug: "people",
    label: "人口与就业",
    description: "长期人口底盘、劳动力供给与就业质量",
  },
  {
    slug: "monetary",
    label: "货币、信用与利率",
    description: "信用扩张、流动性、政策利率与收益率曲线",
  },
  {
    slug: "markets",
    label: "市场与外部",
    description: "资产价格、汇率、出口与外部金融条件",
  },
];

const DATA_SECTIONS = COUNTRY_SECTIONS.filter((section) => section.slug !== "analysis");

export function sectionsForRegion(region: CountryRegion) {
  return region === "cn" ? COUNTRY_SECTIONS : DATA_SECTIONS;
}

export function defaultSectionForRegion(region: CountryRegion): CountrySectionSlug {
  return region === "cn" ? "analysis" : "growth";
}

const SPECIAL_INDICATOR_REGIONS: Record<string, CountryRegion> = {
  SSE: "cn",
  DJI: "us",
  NKY: "jp",
  STOXX50: "eu",
  FTSE100: "uk",
  KOSPI: "kr",
  USDCNY: "us",
  JPYCNY: "jp",
  EURCNY: "eu",
  GBPCNY: "uk",
  KRWCNY: "kr",
};

const INDEX_CODES = new Set(["SSE", "DJI", "NKY", "STOXX50", "FTSE100", "KOSPI"]);

const PREFIX_REGIONS: Record<string, CountryRegion> = {
  CN: "cn",
  US: "us",
  JP: "jp",
  EU: "eu",
  GB: "uk",
  KR: "kr",
};

function indicatorRegion(code: string): CountryRegion | null {
  if (SPECIAL_INDICATOR_REGIONS[code]) return SPECIAL_INDICATOR_REGIONS[code];
  const prefix = code.match(/^(CN|US|JP|EU|GB|KR)_/)?.[1];
  return prefix ? PREFIX_REGIONS[prefix] ?? null : null;
}

export function countrySectionForIndicator(
  code: string,
): { region: CountryRegion; section: CountrySectionSlug } | null {
  const region = indicatorRegion(code);
  if (!region) return null;

  if (/_(?:CORE_)?CPI$|_PPI$|_HOG$/.test(code)) {
    return { region, section: "prices" };
  }
  if (code === "US_NFP") return { region, section: "people" };
  if (
    code === "CN_TSF" ||
    /_(?:FFR|BOJ|BOJ_ASSETS|ECB|ECB_ASSETS|BOE|M3)$/.test(code) ||
    /_(?:M0|M1|M2|BASE)(?:_|$)/.test(code) ||
    /_(?:2Y|5Y|10Y|30Y|10Y2Y)$/.test(code)
  ) {
    return { region, section: "monetary" };
  }
  if (
    INDEX_CODES.has(code) ||
    code.endsWith("CNY") ||
    /_(?:EXPORTS|EXPORTS_ABS|ENERGY|RESERVES)$/.test(code)
  ) {
    return { region, section: "markets" };
  }
  return { region, section: "growth" };
}

export function countrySectionForPath(
  pathname: string,
): { region: CountryRegion; section: CountrySectionSlug } | null {
  const countryMatch = pathname.match(/^\/country\/(cn|us|jp|eu|uk|kr)(?:\/([^/]+))?$/);
  if (countryMatch) {
    const region = countryMatch[1] as CountryRegion;
    const requested = countryMatch[2] as CountrySectionSlug | undefined;
    const section = requested ?? defaultSectionForRegion(region);
    return sectionsForRegion(region).some((item) => item.slug === section)
      ? { region, section }
      : null;
  }

  const detailMatch = pathname.match(/^\/(population|employment|money-supply|bonds)\/(cn|us|jp|eu|uk|kr)$/);
  if (detailMatch) {
    return {
      region: detailMatch[2] as CountryRegion,
      section: detailMatch[1] === "population" || detailMatch[1] === "employment"
        ? "people"
        : "monetary",
    };
  }
  if (pathname.startsWith("/analysis/cn/")) return { region: "cn", section: "analysis" };
  if (pathname.startsWith("/topics/cny-jpy")) return { region: "jp", section: "markets" };

  const indicatorMatch = pathname.match(/^\/indicators\/([^/]+)/);
  return indicatorMatch
    ? countrySectionForIndicator(decodeURIComponent(indicatorMatch[1]))
    : null;
}
