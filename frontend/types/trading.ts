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
  sharpe?: number;
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
  isConnected: boolean;
  lastUpdated: Date;
}
