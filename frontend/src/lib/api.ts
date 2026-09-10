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

export interface PopulationPoint {
  year: number;
  value: number;
}

export interface PopulationSeries {
  key: string;
  name: string;
  unit: string;
  points: PopulationPoint[];
}

export interface PopulationPyramidBar {
  age_group: string;
  male: number;
  female: number;
  total: number;
}

export interface PopulationDashboard {
  region: string;
  country: string;
  source: string;
  source_url: string;
  last_updated: string | null;
  series: PopulationSeries[];
  pyramid_year: number | null;
  pyramid: PopulationPyramidBar[];
}

export function getPopulation(region: string): Promise<PopulationDashboard> {
  return apiFetch(`/api/population/${region}`);
}

export interface EmploymentPoint {
  period: string;
  value: number;
}

export interface EmploymentSeries {
  key: string;
  name: string;
  unit: string;
  frequency: string;
  source_type: string;
  scope: string;
  points: EmploymentPoint[];
}

export interface EmploymentSnapshot {
  year: number;
  employment_total: number;
  urban_employment: number;
  rural_employment: number;
  migrant_workers: number;
  annual_urban_unemployment: number;
  weekly_hours: number;
  source: string;
  source_url: string;
}

export interface EmploymentSource {
  name: string;
  url: string;
  description: string;
}

export interface EmploymentDashboard {
  region: string;
  country: string;
  latest_month: string | null;
  wdi_updated: string | null;
  monthly_series: EmploymentSeries[];
  annual_series: EmploymentSeries[];
  official_snapshot: EmploymentSnapshot;
  sources: EmploymentSource[];
  warnings: string[];
}

export function getEmployment(region: string): Promise<EmploymentDashboard> {
  return apiFetch(`/api/employment/${region}`);
}
