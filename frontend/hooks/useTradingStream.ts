"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { TradingState, StrategyState, Position, AuditRecord } from "@/types/trading";

const DEFAULT_STRATEGIES: StrategyState[] = [
  {
    id: "orb",
    name: "Opening Range Breakout",
    status: "ACTIVE",
    daily_pnl: 0.0,
    win_rate: 0.0,
    trades_count: 0,
    sharpe: null,
    subtitle: "5m / 15m Volatility Expansion",
    description: "Captures institutional opening drives breaking morning high/low with high relative volume.",
  },
  {
    id: "vwap_pullback",
    name: "VWAP Trend Pullback",
    status: "ACTIVE",
    daily_pnl: 0.0,
    win_rate: 0.0,
    trades_count: 0,
    sharpe: null,
    subtitle: "Institutional Mean Continuation",
    description: "Enters shallow pullbacks to Anchored VWAP with EMA 20/50 trend alignment.",
  },
  {
    id: "news_momentum",
    name: "Catalyst News Momentum",
    status: "ACTIVE",
    daily_pnl: 0.0,
    win_rate: 0.0,
    trades_count: 0,
    sharpe: null,
    subtitle: "Benzinga Breaking Sentiment",
    description: "Executes instant sentiment breakouts on verified high-confidence news catalysts.",
  },
  {
    id: "mean_reversion",
    name: "Statistical Mean Reversion",
    status: "ACTIVE",
    daily_pnl: 0.0,
    win_rate: 0.0,
    trades_count: 0,
    sharpe: null,
    subtitle: "Bollinger / RSI Extreme Exhaustion",
    description: "Fades overextended 2.5-sigma bar deviations back into the 20-period moving average.",
  },
];

const INITIAL_STATE: TradingState = {
  timestamp: new Date().toISOString(),
  account: {
    equity: 0.0,
    cash: 0.0,
    buying_power: 0.0,
    daily_pnl: 0.0,
    daily_pnl_pct: 0.0,
    daily_drawdown: 0.0,
    daily_drawdown_pct: 0.0,
    is_circuit_broken: false,
    risk_level: "NORMAL",
    status: "WAITING_FOR_BACKEND",
  },
  market_context: {
    vix: 0.0,
    vix_regime: "UNKNOWN",
    time_phase: "WAITING_FOR_BACKEND",
    market_status: "UNKNOWN",
    sizing_multiplier: 1.0,
  },
  strategies: DEFAULT_STRATEGIES,
  primary_position: null,
  all_positions: [],
  positions_count: 0,
  working_orders_count: 0,
  recent_activity: [],
  ingestion: {},
  recent_news: [],
  ledger_revision: 0,
  persistence: {
    status: "disabled",
    last_checkpoint_at: null,
    restored_at: null,
  },
  isConnected: false,
  lastUpdated: new Date(),
};

export function useTradingStream(wsUrl: string = "ws://127.0.0.1:8005/ws/ui") {
  const [state, setState] = useState<TradingState>(INITIAL_STATE);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [lastError, setLastError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttemptRef = useRef<number>(0);
  const mountedRef = useRef<boolean>(true);

  // Dynamically resolve hostname in browser environment (e.g. window.location.hostname)
  const getResolvedEndpoints = useCallback(() => {
    const isBrowser = typeof window !== "undefined";
    const hostname = isBrowser && window.location.hostname ? window.location.hostname : "127.0.0.1";
    const isSecure = isBrowser && window.location.protocol === "https:";
    const wsProto = isSecure ? "wss:" : "ws:";
    const httpProto = isSecure ? "https:" : "http:";

    const envWsUrl = process.env.NEXT_PUBLIC_WS_URL;

    let resolvedWsUrl = envWsUrl || wsUrl;
    if (!envWsUrl && isBrowser && (wsUrl === "ws://127.0.0.1:8005/ws/ui" || !wsUrl)) {
      const isLocalHost = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
      resolvedWsUrl = isLocalHost
        ? `${wsProto}//${hostname}:8005/ws/ui`
        : `${wsProto}//${window.location.host}/ws/ui`;
    }

    let httpBase = (isBrowser && window.location.port === "3005")
      ? `${httpProto}//${hostname}:8005`
      : `${httpProto}//${isBrowser ? window.location.host : `${hostname}:8005`}`;
    if (resolvedWsUrl.startsWith("ws://") || resolvedWsUrl.startsWith("wss://")) {
      const match = resolvedWsUrl.match(/^wss?:\/\/([^/]+)/);
      if (match) {
        httpBase = `${httpProto}//${match[1]}`;
      }
    }

    return { resolvedWsUrl, httpBase };
  }, [wsUrl]);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (socketRef.current && (socketRef.current.readyState === WebSocket.OPEN || socketRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      const { resolvedWsUrl } = getResolvedEndpoints();
      const ws = new WebSocket(resolvedWsUrl);
      socketRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setIsConnected(true);
        setLastError(null);
        reconnectAttemptRef.current = 0;
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "STATE_UPDATE" || payload.account) {
            setState((prev) => {
              // Merge incoming backend strategies with default visual assets if needed
              const mergedStrategies = (payload.strategies && payload.strategies.length > 0)
                ? payload.strategies.map((s: StrategyState) => {
                    const fallback = DEFAULT_STRATEGIES.find((ds) => ds.id === s.id);
                    return {
                      ...fallback,
                      ...s,
                    };
                  })
                : prev.strategies;

              // Format primary position
              let primary = payload.primary_position;
              if (!primary && payload.all_positions && payload.all_positions.length > 0) {
                const first = payload.all_positions[0];
                primary = {
                  symbol: first.symbol,
                  side: first.side,
                  shares: first.shares ?? first.qty ?? 0,
                  entry_price: first.avg_entry_price || first.entry_price || 0,
                  market_price: first.market_price || 0,
                  market_value: first.market_value || 0,
                  unrealized_pnl: first.unrealized_pnl || 0,
                  unrealized_pnl_pct: first.unrealized_pnl_pct || 0,
                  stop_loss: first.stop_loss,
                  take_profit_1: first.take_profit_1,
                  take_profit_2: first.take_profit_2,
                  strategy_id: first.strategy_id || "ORB",
                  chart_points: first.chart_points,
                  cost_basis: first.cost_basis,
                  realized_pnl: first.realized_pnl,
                };
              }

              return {
                ...prev,
                timestamp: payload.timestamp || new Date().toISOString(),
                account: {
                  ...prev.account,
                  ...(payload.account || {}),
                },
                market_context: {
                  ...prev.market_context,
                  ...(payload.market_context || {}),
                },
                strategies: mergedStrategies,
                primary_position: primary !== undefined ? primary : prev.primary_position,
                  all_positions: payload.all_positions ?? [],
                positions_count: payload.positions_count ?? (primary ? 1 : 0),
                working_orders_count: payload.working_orders_count ?? prev.working_orders_count,
                recent_activity: payload.recent_activity || prev.recent_activity,
                ingestion: payload.ingestion || prev.ingestion,
                recent_news: payload.recent_news || prev.recent_news,
                ledger_revision: payload.ledger_revision ?? prev.ledger_revision,
                persistence: payload.persistence || prev.persistence,
                isConnected: true,
                lastUpdated: new Date(),
              };
            });
          }
        } catch (e) {
          console.error("Failed to parse incoming WebSocket message:", e);
        }
      };

      ws.onerror = () => {
        if (!mountedRef.current) return;
        setLastError("WebSocket connection failed");
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setIsConnected(false);
        socketRef.current = null;

        // Exponential backoff reconnect: min(1000 * 2^attempt, 10000)
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000);
        reconnectAttemptRef.current += 1;
        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, delay);
      };
    } catch (err: any) {
      setLastError(err?.message || "Failed to initialize WebSocket");
      if (mountedRef.current) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000);
        reconnectAttemptRef.current += 1;
        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, delay);
      }
    }
  }, [getResolvedEndpoints]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    // Fallback: poll backend account, positions, and audit if disconnected
    const pollInterval = setInterval(async () => {
      if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
        try {
          const { httpBase } = getResolvedEndpoints();

          // Poll account state
          try {
            const accRes = await fetch(`${httpBase}/api/account`);
            if (accRes.ok) {
              const accData = await accRes.json();
              setState((prev) => ({
                ...prev,
                account: {
                  ...prev.account,
                  equity: accData.equity ?? prev.account.equity,
                  cash: accData.cash ?? prev.account.cash,
                  buying_power: accData.buying_power ?? prev.account.buying_power,
                  daily_pnl: (accData.realized_pnl ?? 0) + (accData.unrealized_pnl ?? 0),
                  daily_pnl_pct: accData.equity > 0 ? ((accData.realized_pnl ?? 0) + (accData.unrealized_pnl ?? 0)) / accData.equity : 0,
                  daily_drawdown: accData.daily_drawdown_dollars ?? prev.account.daily_drawdown,
                  daily_drawdown_pct: accData.daily_drawdown_pct ?? prev.account.daily_drawdown_pct,
                  is_circuit_broken: accData.is_circuit_broken ?? prev.account.is_circuit_broken,
                  status: accData.status ?? prev.account.status,
                },
              }));
            }
          } catch {
            // Ignore account poll error
          }

          // Poll positions
          try {
            const posRes = await fetch(`${httpBase}/api/positions`);
            if (posRes.ok) {
              const posData = await posRes.json();
              const posList = Object.values(posData).map((p: any) => ({
                symbol: p.symbol,
                side: p.side,
                shares: p.shares ?? p.qty ?? 0,
                entry_price: p.avg_entry_price ?? p.entry_price ?? 0,
                market_price: p.market_price ?? 0,
                market_value: p.market_value ?? 0,
                unrealized_pnl: p.unrealized_pnl ?? 0,
                unrealized_pnl_pct: p.unrealized_pnl_pct ?? 0,
                stop_loss: p.stop_loss,
                take_profit_1: p.take_profit_1,
                take_profit_2: p.take_profit_2,
                strategy_id: p.strategy_id || "ORB",
                chart_points: p.chart_points,
                cost_basis: p.cost_basis,
                realized_pnl: p.realized_pnl,
              }));
              setState((prev) => ({
                ...prev,
                all_positions: posList,
                primary_position: posList.length > 0 ? posList[0] : null,
                positions_count: posList.length,
              }));
            }
          } catch {
            // Ignore positions poll error
          }

          const res = await fetch(`${httpBase}/api/audit?limit=10`);
          if (res.ok) {
            const logs = await res.json();
            if (Array.isArray(logs) && logs.length > 0) {
              setState((prev) => ({
                ...prev,
                recent_activity: logs.map((l: any, i: number) => ({
                  id: String(l.order_id || i),
                  timestamp: l.timestamp ? new Date(l.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
                  type: l.event_trigger || "EXEC",
                  symbol: l.symbol,
                  message: `${l.from_state || ""} -> ${l.to_state || ""} (${l.event_trigger || "EXEC"}${l.reason ? `: ${l.reason}` : ""})`,
                  price: l.fill_price,
                  qty: l.fill_qty,
                })),
              }));
            }
          }
        } catch {
          // Backend might be starting or offline, gracefully ignore
        }
      }
    }, 5000);

    return () => {
      mountedRef.current = false;
      clearInterval(pollInterval);
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [connect, getResolvedEndpoints]);

  const sendAction = useCallback((payload: Record<string, any>): boolean => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(payload));
      return true;
    }
    // Fallback REST endpoint if WebSocket is reconnecting
    if (payload.action === "FLATTEN_POSITION" || payload.action === "FLATTEN_ALL") {
      const { httpBase } = getResolvedEndpoints();
      fetch(`${httpBase}/api/flatten`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: payload.symbol }),
      }).catch(console.error);
      return true;
    }
    return false;
  }, [getResolvedEndpoints]);

  const flattenPosition = useCallback((symbol: string): boolean => {
    return sendAction({ action: "FLATTEN_POSITION", symbol });
  }, [sendAction]);

  const flattenAll = useCallback((): boolean => {
    return sendAction({ action: "FLATTEN_ALL" });
  }, [sendAction]);

  const tightenStop = useCallback((symbol: string, newStop: number): boolean => {
    return sendAction({ action: "TIGHTEN_STOP", symbol, new_stop: newStop });
  }, [sendAction]);

  return {
    state,
    isConnected,
    lastError,
    flattenPosition,
    flattenAll,
    tightenStop,
    sendAction,
  };
}
