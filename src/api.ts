import type { DashboardData, DividendItem, PositionItem, RedfolioConfig, RefreshResult, TransactionInput, TransactionItem } from "./types";

let cachedConfig: RedfolioConfig | null = null;

async function getConfig(): Promise<RedfolioConfig> {
  if (cachedConfig) {
    return cachedConfig;
  }
  if (!window.redfolio) {
    throw new Error("RedFolio desktop bridge is not available");
  }
  cachedConfig = await window.redfolio.getConfig();
  return cachedConfig;
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const config = await getConfig();
  const response = await fetch(`${config.baseUrl}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-redfolio-token": config.token,
      ...(options.headers ?? {})
    }
  });

  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export const api = {
  async transactions(): Promise<TransactionItem[]> {
    const body = await requestJson<{ items: TransactionItem[] }>("/api/transactions");
    return body.items;
  },
  async createTransaction(payload: TransactionInput): Promise<TransactionItem> {
    return requestJson<TransactionItem>("/api/transactions", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  async deleteTransaction(id: number): Promise<void> {
    await requestJson<{ ok: boolean }>(`/api/transactions/${id}`, { method: "DELETE" });
  },
  async positions(): Promise<PositionItem[]> {
    const body = await requestJson<{ items: PositionItem[] }>("/api/positions");
    return body.items;
  },
  async dashboard(): Promise<DashboardData> {
    return requestJson<DashboardData>("/api/dashboard");
  },
  async dividends(): Promise<DividendItem[]> {
    const body = await requestJson<{ items: DividendItem[] }>("/api/dividends");
    return body.items;
  },
  async refresh(): Promise<RefreshResult> {
    return requestJson("/api/refresh", { method: "POST" });
  }
};

