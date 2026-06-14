export type SecurityType = "STOCK" | "ETF";
export type TransactionSide = "BUY" | "SELL";

export interface RedfolioConfig {
  baseUrl: string;
  token: string;
}

export interface TransactionItem {
  id: number;
  instrumentId: number;
  code: string;
  name: string;
  securityType: SecurityType;
  side: TransactionSide;
  tradeDate: string;
  quantity: number;
  price: number;
  fees: number;
  note: string;
}

export interface TransactionInput {
  code: string;
  securityType: SecurityType;
  name?: string;
  side: TransactionSide;
  tradeDate: string;
  quantity: number;
  price: number;
  fees: number;
  note: string;
}

export interface ForecastLine {
  kind: "announced" | "estimated";
  exDate: string | null;
  payDate: string | null;
  cashPerShare: number;
  quantity: number;
  amount: number;
}

export interface PositionItem {
  instrumentId: number;
  code: string;
  name: string;
  securityType: SecurityType;
  exchange: string;
  industry: string;
  quantity: number;
  averageCost: number;
  costBasis: number;
  lastPrice: number | null;
  marketValue: number;
  unrealizedPnl: number;
  ttmCashPerShare: number;
  currentYield: number | null;
  costYield: number | null;
  forecastIncome: number;
  forecastStatus: "none" | "announced" | "estimated" | "mixed";
  forecastLines: ForecastLine[];
  dataAsOf: string | null;
}

export interface DividendItem {
  id: number;
  instrumentId: number;
  code: string;
  name: string;
  securityType: SecurityType;
  exDate: string;
  payDate: string | null;
  recordDate: string | null;
  cashPerShare: number;
  source: string;
  status: string;
}

export interface DashboardTotals {
  marketValue: number;
  costBasis: number;
  forecastIncome: number;
  unrealizedPnl: number;
  currentYield: number | null;
  costYield: number | null;
}

export interface ChartSlice {
  label: string;
  value: number;
}

export interface DashboardData {
  totals: DashboardTotals;
  positions: PositionItem[];
  byType: ChartSlice[];
  byIndustry: ChartSlice[];
  dividendContribution: ChartSlice[];
}

export interface RefreshItem {
  code: string;
  status: "ok" | "partial" | "failed";
  message: string;
}

export interface RefreshResult {
  items: RefreshItem[];
  refreshed: number;
  partial: number;
  failed: number;
}

declare global {
  interface Window {
    redfolio?: {
      getConfig: () => Promise<RedfolioConfig>;
    };
  }
}
