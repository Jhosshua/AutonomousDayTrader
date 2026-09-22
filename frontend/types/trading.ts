export interface AccountState {
  equity: number;
  cash: number;
  buying_power: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  daily_drawdown: number;
  daily_drawdown_pct: number;
  is_circuit_broken: boolean;
  risk_level: string;
  status: string;
}

export interface MarketContext {
  vix: number;
  vix_regime: string;
  time_phase: string;
  market_status: string;
  sizing_multiplier?: number;
}

export interface StrategyState {
  id: string;
  name: string;
  status: string;
  daily_pnl: number;
  win_rate: number;
  trades_count: number;
  sharpe?: number | null;
  subtitle?: string;
  description?: string;
}

export interface ChartPoint {
  time: string | number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Position {
  symbol: string;
  side: "LONG" | "SHORT" | string;
  shares: number;
  entry_price: number;
  market_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss?: number;
  take_profit_1?: number;
  take_profit_2?: number;
  strategy_id?: string;
  chart_points?: ChartPoint[];
  cost_basis?: number;
  realized_pnl?: number;
}

export interface AuditRecord {
  id?: string;
  timestamp: string;
  type: string;
  symbol?: string;
  message: string;
  price?: number;
  qty?: number;
  pnl?: number;
  side?: string;
}

export interface PersistenceStatus {
  status: "durable" | "recovery_halt" | "disabled" | string;
  last_checkpoint_at: string | null;
  restored_at: string | null;
  schema_version?: number;
}

export interface TradeRecord {
  trade_id: string;
  session_date: string;
  symbol: string;
  side: "LONG" | "SHORT" | string;
  status: string;
  strategy_id: string;
  opened_at: string;
  closed_at: string;
  quantity: number;
  avg_entry_price: number;
  avg_exit_price: number;
  realized_pnl: number;
  fees: number;
  exit_reason: string;
  aggregate_only?: boolean;
  fill_legs?: Array<{
    fill_id: string;
    order_id: string;
    side: string;
    qty: number;
    price: number;
    fee: number;
    realized_pnl: number;
    timestamp: string;
  }>;
}

export interface RecoveredSessionSummary {
  session_date: string;
  opening_equity: number;
  closing_equity: number;
  realized_pnl: number;
  trades_count: number;
  fees: number;
  source: string;
  aggregate_only: boolean;
  strategies?: Record<string, { trades_count: number; realized_pnl: number }>;
  note?: string;
}

export interface TradeHistoryResponse {
  as_of: string;
  timezone: string;
  persistence: PersistenceStatus;
  summary: {
    opening_equity: number;
    current_equity: number;
    realized_pnl: number;
    fees: number;
    fees_known: boolean;
    trades_count: number;
    wins: number;
    losses: number;
    win_rate: number;
  };
  items: TradeRecord[];
  recovered_sessions: RecoveredSessionSummary[];
  next_cursor: string | null;
}

export interface NewsItem {
  headline: string;
  symbols: string[];
  score: number;
  confidence: number;
  category: string;
  created_at: string;
}

export interface TradingState {
  type?: string;
  timestamp: string;
  account: AccountState;
  market_context: MarketContext;
  strategies: StrategyState[];
  primary_position: Position | null;
  all_positions: Position[];
  positions_count: number;
  working_orders_count: number;
  recent_activity: AuditRecord[];
  ingestion: Record<string, string>;
  recent_news: NewsItem[];
  ledger_revision: number;
  persistence: PersistenceStatus;
  isConnected: boolean;
  lastUpdated: Date;
}
