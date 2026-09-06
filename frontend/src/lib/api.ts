const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface IndicatorSummary {
  code: string;
  name: string;
  category: string;
  region: string;
  unit: string;
  latest_date: string | null;
  latest_value: number | null;
  change_pct: number | null;
  recent_values: number[];
}

export interface DataPoint {
  date: string;
  value: number;
}

export interface IndicatorHistory {
  code: string;
  name: string;
  unit: string;
  points: DataPoint[];
}

export interface ForecastPoint {
  date: string;
  value: number;
  lower: number;
  upper: number;
}

export interface ForecastOut {
  code: string;
  name: string;
  history: DataPoint[];
  forecast: ForecastPoint[];
}

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getIndicators(region?: string): Promise<IndicatorSummary[]> {
  return apiFetch(region ? `/api/indicators?region=${region}` : "/api/indicators");
}

export function getIndicatorHistory(code: string): Promise<IndicatorHistory> {
  return apiFetch(`/api/indicators/${code}/history`);
}

export function getIndicatorForecast(code: string, horizon = 30): Promise<ForecastOut> {
  return apiFetch(`/api/indicators/${code}/forecast?horizon=${horizon}`);
}

export interface CurrencyOption {
  code: string;
  name: string;
}

export interface ForexHistory {
  base: string;
  target: string;
  points: DataPoint[];
}

export interface ForexForecast {
  base: string;
  target: string;
  history: DataPoint[];
  forecast: ForecastPoint[];
}

export function getCurrencies(): Promise<CurrencyOption[]> {
  return apiFetch("/api/forex/currencies");
}

export function getForexHistory(base: string, target: string): Promise<ForexHistory> {
  return apiFetch(`/api/forex/history?base=${base}&target=${target}`);
}

export function getForexForecast(base: string, target: string, horizon = 30): Promise<ForexForecast> {
  return apiFetch(`/api/forex/forecast?base=${base}&target=${target}&horizon=${horizon}`);
}
