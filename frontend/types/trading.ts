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

export interface StrategyWindow {
  state: "CAN_TRADE" | "LIMITED" | "BLOCKED" | "WAITING" | "DONE_FOR_DAY" | "MARKET_CLOSED" | "PAUSED" | string;
  headline: string;
  can_open_now: boolean;
  in_hours: boolean;
  hours: string;
  /** B5: ["HH:MM","HH:MM"] ET pairs describing the trading schedule (present regardless of trading_day). */
  ranges?: [string, string][];
  /** B5: false on weekends/holidays; ranges still describe the schedule. */
  trading_day?: boolean;
  schedule_text: string;
  next_change_at: string | null;
  blockers: string[];
  limits?: string[];
  market_text: string;
  notes: string[];
  evaluated_at: string;
}

export interface StrategyDecisionSummary {
  signals_today: number;
  orders_today: number;
  blocked_today: number;
  top_block_reason: string | null;
  top_block_text: string | null;
  blocked_by_reason: Record<string, number>;
}

export interface StrategyState {
  id: string;
  name: string;
  status: string;
  window?: StrategyWindow;
  decisions?: StrategyDecisionSummary;
  daily_pnl: number;
  win_rate: number;
  trades_count: number;
  sharpe?: number | null;
  subtitle?: string;
  description?: string;
  tri_engine?: {
    version: string;
    symbol: string;
    phase: string;
    reason: string | null;
    quantity: number;
    side: string | null;
    mode: string;
    risk_reserved: number;
    risk_budget: number | null;
    entry_due: string | null;
    incomplete: boolean;
    tranches: FixedTranche[];
  };
  or15?: {
    phase: string;
    reason: string | null;
    quantity: number;
    mode: string;
    or_high: number | null;
    or_low: number | null;
    target_price: number | null;
    exit_due: string | null;
    protection_confirmed: boolean;
    version: string;
    incomplete: boolean;
  };
}

export interface FixedTranche {
  id: number;
  qty: number;
  closed_qty: number;
  target: number;
  target_r: number;
  stop: number;
  exit_due: string;
  exit_reason: string | null;
  protection_confirmed: boolean;
  protection_terminal: boolean;
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
  tranches?: FixedTranche[];
  symbol: string;
  side: "LONG" | "SHORT" | string;
  shares: number;
  entry_price: number;
  market_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  /** F1: nullable; a position can be open with no protective exit set yet. */
  stop_loss?: number | null;
  take_profit_1?: number;
  take_profit_2?: number;
  strategy_id?: string;
  chart_points?: ChartPoint[];
  cost_basis?: number;
  realized_pnl?: number;
  entry_atr?: number | null;
  entry_date?: string | null;
  fixed_protection?: boolean;
  exit_due?: string | null;
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

export interface SwingCandidate {
  symbol: string;
  /** F11: the data date this scan is based on (never invent a value when missing). */
  date?: string | null;
  price: number;
  close: number;
  sma_200: number;
  sma_200_pass: boolean;
  above_200_sma: boolean;
  rs_stock_60d: number;
  rs_qqq_60d: number;
  rs_60d_stock: number;
  rs_60d_qqq: number;
  relative_strength_ok: boolean;
  rs_pass: boolean;
  rsi_2: number;
  rsi_pass: boolean;
  panic_trigger: boolean;
  earnings_blackout: boolean;
  earnings_date: string | null;
  next_earnings_date: string | null;
  daily_atr_14: number;
  atr_14: number;
  qualified: boolean;
  is_held: boolean;
  is_staged: boolean;
  status: "QUALIFIED" | "STAGED" | "ACTIVE" | "WATCHING" | "INELIGIBLE" | "BLOCKED" | string;
  rejection_reasons?: string[];
}

export interface SwingPosition {
  symbol: string;
  side: "LONG" | string;
  shares: number;
  entry_price: number;
  market_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss: number;
  stop_loss_price: number;
  atr_14: number;
  entry_atr?: number | null;
  atr_stop_distance: number;
  atr_stop_pct: number;
  entry_date?: string | null;
  holding_days: number;
  max_holding_days: number;
  holding_progress: string;
  sma_5: number;
  rsi_2: number;
  exit_triggers: {
    sma_5_cross: boolean;
    rsi_70_cross: boolean;
    time_stop_day_5: boolean;
    earnings_tomorrow: boolean;
  };
  staged_exit_at_open: boolean;
}

export interface SwingEngineState {
  status: "ACTIVE" | "SCANNING" | "STANDBY" | "IDLE" | string;
  strategy_name?: string;
  allocated_capital: number;
  slot_notional: number;
  max_slots: number;
  active_slots_used: number;
  available_slots: number;
  flattening_exempt: boolean;
  candidates: SwingCandidate[];
  positions: SwingPosition[];
  last_scan_time: string | null;
  schedule_text?: string;
  last_close_data_note?: string | null;
  last_close_entries_withheld?: boolean;
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
  broker?: BrokerInfo;
  recent_news: NewsItem[];
  ledger_revision: number;
  persistence: PersistenceStatus;
  swing?: SwingEngineState;
  isConnected: boolean;
  lastUpdated: Date;
}


export interface BrokerInfo {
  mode: "simulated" | "alpaca_paper" | string;
  account_number: string | null;
  mismatch: boolean;
}
