// @steered SNARE-2 2026-09-30
export interface AccountState {
  equity: number;
  cash: number;
  buying_power: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  /** Opening account equity for the current Eastern trading date. */
  daily_starting_equity?: number;
  daily_drawdown: number;
  daily_drawdown_pct: number;
  is_circuit_broken: boolean;
  risk_level: string;
  status: string;
  /** S14: the loss stop's own drawdown, net of today's overnight result (new backend only). */
  risk_drawdown?: number | null;
  /** D6: today's overnight holds result, kept out of the daily loss limit (new backend only). */
  overnight_realized_today?: number | null;
}

export interface VixTier {
  name: string;
  lower: number | null;
  upper: number | null;
  sizing: number;
  stop: number;
}

/** Every key after `sizing_multiplier` is optional so an older backend during a deploy still renders;
 * the UI treats a missing key as unknown. A new backend always sends every key (null when unknown). */
export interface MarketContext {
  vix: number | null;
  vix_regime: string;
  time_phase: string;
  market_status: string;
  sizing_multiplier?: number;
  stop_multiplier?: number | null;
  vix_stale?: boolean | null;
  vix_age_seconds?: number | null;
  time_multiplier?: number | null;
  market_trend?: string | null;
  market_trend_reason?: string | null;
  max_concurrent_positions?: number | null;
  notional_cap_pct?: number | null;
  base_risk_pct?: number | null;
  midday?: { start: string; end: string } | null;
  vix_tiers?: VixTier[] | null;
  adaptive_strategies?: string[] | null;
}

/** What the robot saw when it bought (backend entry_context). Null on trades from before it was kept. */
export interface EntryContext {
  decided_at?: string | null;
  time_phase?: string | null;
  time_multiplier?: number | null;
  vix?: number | null;
  vix_regime?: string | null;
  sizing_multiplier?: number | null;
  stop_multiplier?: number | null;
  vix_stale?: boolean | null;
  qty_adaptation?: number | null;
  qty_final?: number | null;
  qty_if_neutral?: number | null;
  size_limited_by?: "notional_cap" | "risk" | "account_limits" | string | null;
  market_trend?: string | null;
  trend_reason?: string | null;
  stop_raw?: number | null;
  stop_adapted?: number | null;
  /** Ride the Trend only */
  stop_basis?: "volatility" | "structure" | "floor" | string | null;
  rs?: { day: boolean; recent: boolean } | null;
  macro?: string | null;
  regime_enforced?: string[] | null;
}

/** ORB's 9:38 market check and rules for one trade (backend orb_context). Fields are null when unknown. */
export interface OrbContext {
  classification: string | null;
  short_frac: number | null;
  wave: string | null;
  decided_at: string | null;
  short_bounds: { min: number; max: number } | null;
  flow_rules_on: string[] | null;
  breakeven_r: number | null;
  risk_usd: number | null;
  flatten_at: string | null;
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
  mode?: string;
  addons_enforced?: boolean;
  profiles_ready?: { ready: number; total: number; missing: string[] };
  last_block_by_symbol?: Record<string, { event: string; bar: string; detail?: Record<string, unknown> }>;
  tri_engine?: {
    version: string;
    symbol: string;
    phase: string;
    reason: string | null;
    last_error?: string | null;
    quantity: number;
    side: string | null;
    mode: string;
    risk_reserved: number;
    risk_budget: number | null;
    entry_due: string | null;
    incomplete: boolean;
    tranches: FixedTranche[];
  };
  orb?: {
    mode: string;
    mode_text: string;
    step: string | null;
    ready: boolean | null;
    hours: string;
    picks: { symbol: string; direction: string; tier: string | null }[];
    open_trades: {
      symbol: string;
      direction: string;
      qty: number;
      avg_price: number | null;
      stop: number | null;
      target: number | null;
      r: number | null;
      last_price: number | null;
      breakeven_locked: boolean | null;
      exit_requested: string | null;
      status: string;
    }[];
    realized_pnl: number;
    unrealized_pnl: number;
    open_risk: number | null;
    errors: { kind?: string; alarm?: string; err?: string; reason?: string; note?: string; symbol?: string }[];
    init_error: string | null;
    alerts?: string[];
    orphans?: { symbol: string; text: string }[];
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
  entry_context?: EntryContext | null;
  initial_stop?: number | null;
  bracket_status?: string | null;
  runner_policy?: string | null;
  target_1_filled?: boolean | null;
  orb_context?: OrbContext | null;
  /** Fixed plans: percent of the account the plan risks (0.75 = 0.75%). */
  plan_risk_pct?: number | null;
  r_multiple?: number | null;
  /** True for an overnight hold (NVDA, IREN, HUT overnight). It sells at exit_due, the next 9:30 AM open. */
  overnight?: boolean;
}

/** One stock of the overnight holds (backend overnight payload "rows"). The latest night of that stock. */
export interface OvernightRow {
  symbol: string;
  name: string;
  strategy_id: string;
  enabled: boolean;
  state: string | null;
  buy_date: string | null;
  sale_date: string | null;
  reason: string | null;
  block: string | null;
  needs_look: string[];
  qty: number | null;
  held_qty: number;
  buy_avg: number | null;
  realized: number | null;
  reserved: boolean;
  tonight: boolean;
  sale_text: string | null;
  size_note: string;
}

export interface OvernightHold {
  symbol: string;
  strategy_id: string;
  shares: number;
  buy_avg: number | null;
  buy_date: string;
  sale_date: string | null;
  nights: "weeknight" | "weekend" | "holiday" | string | null;
  state: string;
  needs_look: string[];
}

/** GET /api/overnight and the websocket frame's "overnight" key. Null when the backend could not build it. */
export interface OvernightPayload {
  state: { mode: string; running: boolean; init_error?: string | null; realized_today?: number; unsold_after_0931?: string[] };
  settings: { mode: string; enabled: string[]; pct: number; cap: number; room_multiple: number };
  rows: OvernightRow[];
  holds: OvernightHold[];
  skips: { symbol: string; buy_date: string; reason: string; wanted_qty: number | null }[];
  intents: unknown[];
  queued_sales: unknown[];
  no_buy_tonight?: boolean;
  no_buy_until?: string;
  unsold_after_0931?: string[];
  /** X6: day trades closed early for a closing auction buy, last 5 days (new backend only). */
  x6?: OvernightX6[];
  /** Today's calendar as the closing buy judges it: full_day false on an early close or a closed market (new backend only). */
  today?: { date: string; full_day: boolean; reason: string | null; sale_date: string | null };
}

export interface OvernightX6 {
  symbol: string;
  date: string;
  qty: number | null;
  side: string | null;
  done: boolean;
  order_id: string | null;
  /** The day playbook whose trade was closed (null on jobs saved before 2026-10-05). */
  strategy_id: string | null;
  filled_at: string | null;
  /** Shares actually closed at Alpaca (0 when nothing was sent, e.g. Alpaca already held none). */
  filled_qty?: number;
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
  /** Account equity movement for this session. */
  account_change?: number;
  /** Finished trade result. Null when detailed trade coverage is incomplete. */
  finished_trade_result?: number | null;
  realized_pnl: number;
  trades_count: number;
  wins?: number | null;
  losses?: number | null;
  fees: number;
  fees_known?: boolean;
  source: string;
  aggregate_only: boolean;
  trade_detail_complete?: boolean;
  strategies?: Record<string, {
    trades_count: number;
    realized_pnl: number;
    wins?: number;
    losses?: number;
  }>;
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
  sessions?: RecoveredSessionSummary[];
  coverage?: {
    detailed_trade_rows: number;
    session_count: number;
  };
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
  overnight?: OvernightPayload | null;
  isConnected: boolean;
  lastUpdated: Date;
}


export interface BrokerInfo {
  mode: "simulated" | "alpaca_paper" | string;
  account_number: string | null;
  mismatch: boolean;
}
