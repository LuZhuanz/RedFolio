import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  Database,
  LayoutDashboard,
  ListOrdered,
  PieChart,
  Plus,
  RefreshCw,
  Trash2,
  WalletCards
} from "lucide-react";
import { api } from "./api";
import type {
  ChartSlice,
  DashboardData,
  DividendItem,
  PositionItem,
  SecurityType,
  TransactionInput,
  TransactionItem,
  TransactionSide
} from "./types";

const emptyDashboard: DashboardData = {
  totals: {
    marketValue: 0,
    costBasis: 0,
    forecastIncome: 0,
    unrealizedPnl: 0,
    currentYield: null,
    costYield: null
  },
  positions: [],
  byType: [],
  byIndustry: [],
  dividendContribution: []
};

const today = new Date().toISOString().slice(0, 10);
const chartColors = ["#b2272b", "#2474a6", "#2f855a", "#b7791f", "#6b46c1", "#4a5568"];
const otherColor = "#9aa0a6";

type HoldingSlice = {
  label: string;
  detail: string;
  value: number;
  other?: boolean;
};

function currency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2
  }).format(value);
}

function number(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: digits
  }).format(value);
}

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }
  return `${(value * 100).toFixed(2)}%`;
}

function statusLabel(status: PositionItem["forecastStatus"]): string {
  const labels = {
    none: "无分红记录",
    announced: "已公告",
    estimated: "历史估算",
    mixed: "公告+估算"
  };
  return labels[status];
}

function buildHoldingDistribution(positions: PositionItem[], limit = 8): HoldingSlice[] {
  const sorted = [...positions]
    .filter((position) => position.marketValue > 0)
    .sort((a, b) => b.marketValue - a.marketValue);
  const visible: HoldingSlice[] = sorted.slice(0, limit).map((position) => ({
    label: position.name || position.code,
    detail: position.code,
    value: position.marketValue
  }));
  const hidden = sorted.slice(limit);
  const otherValue = hidden.reduce((sum, position) => sum + position.marketValue, 0);

  if (otherValue > 0) {
    visible.push({
      label: "其他",
      detail: `${hidden.length} 个标的`,
      value: otherValue,
      other: true
    });
  }

  return visible;
}

function donutGradient(items: HoldingSlice[], total: number): string {
  if (total <= 0) {
    return "#ece8df";
  }

  let cursor = 0;
  const segments = items.map((item, index) => {
    const start = cursor;
    const end = cursor + (Math.max(item.value, 0) / total) * 100;
    cursor = end;
    const color = item.other ? otherColor : chartColors[index % chartColors.length];
    return `${color} ${start.toFixed(4)}% ${end.toFixed(4)}%`;
  });

  return `conic-gradient(${segments.join(", ")})`;
}

type TabKey = "dashboard" | "positions" | "transactions" | "dividends";

export function App() {
  const [tab, setTab] = useState<TabKey>("dashboard");
  const [dashboard, setDashboard] = useState<DashboardData>(emptyDashboard);
  const [transactions, setTransactions] = useState<TransactionItem[]>([]);
  const [dividends, setDividends] = useState<DividendItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");

  async function loadAll() {
    setError("");
    const [nextDashboard, nextTransactions, nextDividends] = await Promise.all([
      api.dashboard(),
      api.transactions(),
      api.dividends()
    ]);
    setDashboard(nextDashboard);
    setTransactions(nextTransactions);
    setDividends(nextDividends);
  }

  useEffect(() => {
    loadAll()
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    setError("");
    setMessage("");
    try {
      const result = await api.refresh();
      await loadAll();
      setMessage(`完整刷新 ${result.refreshed} 个，部分成功 ${result.partial} 个，失败 ${result.failed} 个。`);
      const problemItems = result.items.filter((item) => item.status !== "ok");
      if (problemItems.length > 0) {
        setError(problemItems.map((item) => `${item.code}: ${item.message}`).join("\n"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }

  async function handleCreateTransaction(payload: TransactionInput) {
    setError("");
    setMessage("");
    try {
      await api.createTransaction(payload);
      await loadAll();
      setMessage("交易流水已保存。");
      setTab("positions");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDeleteTransaction(id: number) {
    setError("");
    setMessage("");
    try {
      await api.deleteTransaction(id);
      await loadAll();
      setMessage("交易流水已删除。");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const tabs = [
    { key: "dashboard" as const, label: "总览", icon: LayoutDashboard },
    { key: "positions" as const, label: "持仓", icon: PieChart },
    { key: "transactions" as const, label: "流水", icon: ListOrdered },
    { key: "dividends" as const, label: "分红", icon: CalendarDays }
  ];

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">R</div>
          <div>
            <h1>RedFolio</h1>
            <p>红利持仓工作台</p>
          </div>
        </div>

        <nav className="nav">
          {tabs.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={tab === item.key ? "nav-item active" : "nav-item"}
                onClick={() => setTab(item.key)}
                type="button"
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <button className="refresh-button" onClick={handleRefresh} disabled={refreshing} type="button">
          <RefreshCw size={18} className={refreshing ? "spin" : ""} />
          <span>{refreshing ? "刷新中" : "手动刷新"}</span>
        </button>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">A 股股票 / 场内 ETF</p>
            <h2>{tabs.find((item) => item.key === tab)?.label}</h2>
          </div>
          <div className="topbar-status">
            <Database size={17} />
            <span>本机 SQLite</span>
          </div>
        </header>

        {error && <div className="notice error">{error}</div>}
        {message && <div className="notice success">{message}</div>}

        {loading ? (
          <div className="empty-state">正在连接本地数据服务...</div>
        ) : (
          <>
            {tab === "dashboard" && <DashboardView data={dashboard} />}
            {tab === "positions" && <PositionsView positions={dashboard.positions} />}
            {tab === "transactions" && (
              <TransactionsView
                transactions={transactions}
                onCreate={handleCreateTransaction}
                onDelete={handleDeleteTransaction}
              />
            )}
            {tab === "dividends" && <DividendsView dividends={dividends} positions={dashboard.positions} />}
          </>
        )}
      </section>
    </main>
  );
}

function DashboardView({ data }: { data: DashboardData }) {
  return (
    <div className="stack">
      <section className="metrics-grid">
        <Metric title="总市值" value={currency(data.totals.marketValue)} icon={<WalletCards size={20} />} />
        <Metric title="总成本" value={currency(data.totals.costBasis)} />
        <Metric title="预计本年税前红利" value={currency(data.totals.forecastIncome)} />
        <Metric title="成本股息率" value={percent(data.totals.costYield)} />
        <Metric title="当前股息率" value={percent(data.totals.currentYield)} />
        <Metric
          title="浮动盈亏"
          value={currency(data.totals.unrealizedPnl)}
          tone={data.totals.unrealizedPnl >= 0 ? "gain" : "loss"}
        />
      </section>

      {data.positions.length === 0 ? (
        <div className="empty-state">先添加一笔买入流水，持仓和红利预测会在这里汇总。</div>
      ) : (
        <section className="dashboard-grid">
          <HoldingDistributionPanel positions={data.positions} />
          <ChartPanel title="资产类型" items={data.byType} />
          <ChartPanel title="行业 / 类型市值" items={data.byIndustry} />
          <ChartPanel title="红利贡献" items={data.dividendContribution} />
        </section>
      )}
    </div>
  );
}

function Metric({
  title,
  value,
  icon,
  tone
}: {
  title: string;
  value: string;
  icon?: React.ReactNode;
  tone?: "gain" | "loss";
}) {
  return (
    <article className={`metric ${tone ?? ""}`}>
      <div className="metric-title">
        {icon}
        <span>{title}</span>
      </div>
      <strong>{value}</strong>
    </article>
  );
}

function HoldingDistributionPanel({ positions }: { positions: PositionItem[] }) {
  const items = useMemo(() => buildHoldingDistribution(positions), [positions]);
  const total = items.reduce((sum, item) => sum + item.value, 0);
  const top3Value = items
    .filter((item) => !item.other)
    .slice(0, 3)
    .reduce((sum, item) => sum + item.value, 0);
  const largestValue = items[0]?.value ?? 0;

  return (
    <article className="panel holding-panel">
      <div className="panel-title">
        <h3>持仓占比</h3>
        <span>{currency(total)}</span>
      </div>
      {items.length === 0 ? (
        <div className="empty-state compact">暂无数据。</div>
      ) : (
        <div className="holding-layout">
          <div className="donut-side">
            <div className="donut-chart" style={{ background: donutGradient(items, total) }}>
              <div className="donut-hole">
                <span>前 3 持仓</span>
                <strong>{percent(total ? top3Value / total : null)}</strong>
              </div>
            </div>
            <div className="concentration-grid">
              <div>
                <span>最大单仓</span>
                <strong>{percent(total ? largestValue / total : null)}</strong>
              </div>
              <div>
                <span>持仓数</span>
                <strong>{number(positions.length, 0)}</strong>
              </div>
            </div>
          </div>

          <div className="bar-list holding-list">
            {items.map((item, index) => {
              const ratio = total ? item.value / total : 0;
              const colorClass = item.other ? "other" : index % 6;
              return (
                <div className="bar-row" key={`${item.label}-${item.detail}`}>
                  <div className="bar-meta">
                    <span className={`dot dot-${colorClass}`} />
                    <strong>{item.label}</strong>
                    <em>{percent(ratio)}</em>
                  </div>
                  <div className="bar-track">
                    <div className={`bar-fill fill-${colorClass}`} style={{ width: `${Math.max(4, ratio * 100)}%` }} />
                  </div>
                  <span className="bar-value">
                    {item.detail} · {currency(item.value)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </article>
  );
}

function PositionsView({ positions }: { positions: PositionItem[] }) {
  if (positions.length === 0) {
    return <div className="empty-state">暂无持仓。添加买入流水后会自动生成当前持仓。</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>类型</th>
            <th className="numeric">数量</th>
            <th className="numeric">成本价</th>
            <th className="numeric">最新价</th>
            <th className="numeric">市值</th>
            <th className="numeric">参考分红/份</th>
            <th className="numeric">当前股息率</th>
            <th className="numeric">成本股息率</th>
            <th className="numeric">预计本年红利</th>
            <th>口径</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.instrumentId}>
              <td>
                <div className="security-cell">
                  <strong>{position.name}</strong>
                  <span>{position.code}</span>
                </div>
              </td>
              <td>{position.securityType === "ETF" ? "ETF" : "股票"}</td>
              <td className="numeric">{number(position.quantity, 0)}</td>
              <td className="numeric">{number(position.averageCost, 3)}</td>
              <td className="numeric">{number(position.lastPrice, 3)}</td>
              <td className="numeric">{currency(position.marketValue)}</td>
              <td className="numeric">{number(position.ttmCashPerShare, 4)}</td>
              <td className="numeric">{percent(position.currentYield)}</td>
              <td className="numeric">{percent(position.costYield)}</td>
              <td className="numeric">{currency(position.forecastIncome)}</td>
              <td>
                <span className={`pill ${position.forecastStatus}`}>{statusLabel(position.forecastStatus)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TransactionsView({
  transactions,
  onCreate,
  onDelete
}: {
  transactions: TransactionItem[];
  onCreate: (payload: TransactionInput) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  return (
    <div className="split">
      <TransactionForm onCreate={onCreate} />
      <section className="panel">
        <div className="panel-title">
          <h3>交易流水</h3>
          <span>{transactions.length} 条</span>
        </div>
        {transactions.length === 0 ? (
          <div className="empty-state compact">暂无流水。</div>
        ) : (
          <div className="table-wrap embedded">
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>标的</th>
                  <th>方向</th>
                  <th className="numeric">数量</th>
                  <th className="numeric">价格</th>
                  <th className="numeric">费用</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((item) => (
                  <tr key={item.id}>
                    <td>{item.tradeDate}</td>
                    <td>
                      <div className="security-cell">
                        <strong>{item.name}</strong>
                        <span>{item.code}</span>
                      </div>
                    </td>
                    <td>
                      <span className={item.side === "BUY" ? "side buy" : "side sell"}>
                        {item.side === "BUY" ? "买入" : "卖出"}
                      </span>
                    </td>
                    <td className="numeric">{number(item.quantity, 0)}</td>
                    <td className="numeric">{number(item.price, 3)}</td>
                    <td className="numeric">{number(item.fees, 2)}</td>
                    <td className="numeric">
                      <button className="icon-button" onClick={() => onDelete(item.id)} type="button" title="删除">
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function TransactionForm({ onCreate }: { onCreate: (payload: TransactionInput) => Promise<void> }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [securityType, setSecurityType] = useState<SecurityType>("STOCK");
  const [side, setSide] = useState<TransactionSide>("BUY");
  const [tradeDate, setTradeDate] = useState(today);
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [fees, setFees] = useState("0");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await onCreate({
        code,
        name,
        securityType,
        side,
        tradeDate,
        quantity: Number(quantity),
        price: Number(price),
        fees: Number(fees || 0),
        note
      });
      setCode("");
      setName("");
      setQuantity("");
      setPrice("");
      setFees("0");
      setNote("");
      setSide("BUY");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="panel form-panel" onSubmit={submit}>
      <div className="panel-title">
        <h3>新增流水</h3>
        <Plus size={18} />
      </div>

      <div className="field-grid">
        <label>
          <span>证券代码</span>
          <input
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="例如 600519"
            required
            pattern="[0-9.]{6,9}"
          />
        </label>
        <label>
          <span>名称</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="可选" />
        </label>
        <label>
          <span>类型</span>
          <select value={securityType} onChange={(event) => setSecurityType(event.target.value as SecurityType)}>
            <option value="STOCK">股票</option>
            <option value="ETF">场内 ETF</option>
          </select>
        </label>
        <label>
          <span>方向</span>
          <select value={side} onChange={(event) => setSide(event.target.value as TransactionSide)}>
            <option value="BUY">买入</option>
            <option value="SELL">卖出</option>
          </select>
        </label>
        <label>
          <span>交易日期</span>
          <input type="date" value={tradeDate} onChange={(event) => setTradeDate(event.target.value)} required />
        </label>
        <label>
          <span>数量</span>
          <input
            type="number"
            min="0"
            step="1"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            required
          />
        </label>
        <label>
          <span>成交价</span>
          <input
            type="number"
            min="0"
            step="0.001"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
            required
          />
        </label>
        <label>
          <span>费用</span>
          <input type="number" min="0" step="0.01" value={fees} onChange={(event) => setFees(event.target.value)} />
        </label>
      </div>

      <label className="wide-field">
        <span>备注</span>
        <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="可选" />
      </label>

      <button className="primary-button" disabled={saving} type="submit">
        <Plus size={17} />
        <span>{saving ? "保存中" : "保存流水"}</span>
      </button>
    </form>
  );
}

function DividendsView({ dividends, positions }: { dividends: DividendItem[]; positions: PositionItem[] }) {
  const forecastLines = useMemo(
    () =>
      positions.flatMap((position) =>
        position.forecastLines.map((line, index) => ({
          id: `${position.instrumentId}-${index}`,
          code: position.code,
          name: position.name,
          ...line
        }))
      ),
    [positions]
  );

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-title">
          <h3>本年红利预测</h3>
          <span>{forecastLines.length} 条</span>
        </div>
        {forecastLines.length === 0 ? (
          <div className="empty-state compact">刷新分红数据后会显示预测明细。</div>
        ) : (
          <div className="table-wrap embedded">
            <table>
              <thead>
                <tr>
                  <th>标的</th>
                  <th>口径</th>
                  <th>除息日</th>
                  <th className="numeric">每股/份</th>
                  <th className="numeric">数量</th>
                  <th className="numeric">金额</th>
                </tr>
              </thead>
              <tbody>
                {forecastLines.map((line) => (
                  <tr key={line.id}>
                    <td>
                      <div className="security-cell">
                        <strong>{line.name}</strong>
                        <span>{line.code}</span>
                      </div>
                    </td>
                    <td>
                      <span className={`pill ${line.kind}`}>{line.kind === "announced" ? "已公告" : "历史估算"}</span>
                    </td>
                    <td>{line.exDate ?? "--"}</td>
                    <td className="numeric">{number(line.cashPerShare, 4)}</td>
                    <td className="numeric">{number(line.quantity, 0)}</td>
                    <td className="numeric">{currency(line.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-title">
          <h3>历史分红事件</h3>
          <span>{dividends.length} 条</span>
        </div>
        {dividends.length === 0 ? (
          <div className="empty-state compact">暂无分红事件。</div>
        ) : (
          <div className="table-wrap embedded">
            <table>
              <thead>
                <tr>
                  <th>标的</th>
                  <th>除息日</th>
                  <th>发放日</th>
                  <th className="numeric">每股/份现金</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {dividends.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="security-cell">
                        <strong>{item.name}</strong>
                        <span>{item.code}</span>
                      </div>
                    </td>
                    <td>{item.exDate}</td>
                    <td>{item.payDate ?? "--"}</td>
                    <td className="numeric">{number(item.cashPerShare, 4)}</td>
                    <td>{item.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function ChartPanel({ title, items }: { title: string; items: ChartSlice[] }) {
  const total = items.reduce((sum, item) => sum + item.value, 0);

  return (
    <article className="panel chart-panel">
      <div className="panel-title">
        <h3>{title}</h3>
        <span>{currency(total)}</span>
      </div>
      {items.length === 0 ? (
        <div className="empty-state compact">暂无数据。</div>
      ) : (
        <div className="bar-list">
          {items.slice(0, 8).map((item, index) => {
            const ratio = total ? item.value / total : 0;
            return (
              <div className="bar-row" key={item.label}>
                <div className="bar-meta">
                  <span className={`dot dot-${index % 6}`} />
                  <strong>{item.label}</strong>
                  <em>{percent(ratio)}</em>
                </div>
                <div className="bar-track">
                  <div className={`bar-fill fill-${index % 6}`} style={{ width: `${Math.max(4, ratio * 100)}%` }} />
                </div>
                <span className="bar-value">{currency(item.value)}</span>
              </div>
            );
          })}
        </div>
      )}
    </article>
  );
}
