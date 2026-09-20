"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { TradingState, StrategyState, Position, AuditRecord } from "@/types/trading";

const DEFAULT_STRATEGIES: StrategyState[] = [
  {
    id: "orb",
    name: "Opening Range Breakout",
    status: "ACTIVE",
    daily_pnl: 280.0,
    win_rate: 0.68,
    trades_count: 3,
    sharpe: 2.41,
    subtitle: "5m / 15m Volatility Expansion",
    description: "Captures institutional opening drives breaking morning high/low with high relative volume.",
  },
  {
    id: "vwap_pullback",
    name: "VWAP Trend Pullback",
    status: "ACTIVE",
    daily_pnl: 340.0,
    win_rate: 0.62,
    trades_count: 2,
    sharpe: 1.88,
    subtitle: "Institutional Mean Continuation",
    description: "Enters shallow pullbacks to Anchored VWAP with EMA 20/50 trend alignment.",
  },
  {
    id: "news_momentum",
    name: "Catalyst News Momentum",
    status: "STANDBY",
    daily_pnl: 180.0,
    win_rate: 0.71,
    trades_count: 1,
    sharpe: 2.15,
    subtitle: "Benzinga Breaking Sentiment",
    description: "Executes instant sentiment breakouts on verified high-confidence news catalysts.",
  },
  {
    id: "mean_reversion",
    name: "Statistical Mean Reversion",
    status: "COOLDOWN",
    daily_pnl: 0.0,
    win_rate: 0.55,
    trades_count: 0,
    sharpe: 1.65,
    subtitle: "Bollinger / RSI Extreme Exhaustion",
    description: "Fades overextended 2.5-sigma bar deviations back into the 20-period moving average.",
  },
];

const DEFAULT_POSITION: Position = {
  symbol: "NVDA",
  side: "LONG",
  shares: 150,
  entry_price: 124.5,
  market_price: 126.8,
  market_value: 19020.0,
  unrealized_pnl: 345.0,
  unrealized_pnl_pct: 1.85,
  stop_loss: 123.75,
  take_profit_1: 127.5,
  take_profit_2: 129.0,
  strategy_id: "orb",
};

const INITIAL_STATE: TradingState = {
  timestamp: new Date().toISOString(),
  account: {
    equity: 50800.0,
    cash: 48500.0,
    buying_power: 194000.0,
    daily_pnl: 800.0,
    daily_pnl_pct: 1.6,
    daily_drawdown: 0.0,
    daily_drawdown_pct: 0.0,
    is_circuit_broken: false,
    risk_level: "NORMAL",
    status: "HEALTHY",
  },
  market_context: {
    vix: 18.25,
    vix_regime: "NORMAL",
    time_phase: "TREND",
    market_status: "OPEN",
    sizing_multiplier: 1.0,
  },
  strategies: DEFAULT_STRATEGIES,
  primary_position: DEFAULT_POSITION,
  all_positions: [DEFAULT_POSITION],
  positions_count: 1,
  working_orders_count: 2,
  recent_activity: [
    {
      id: "1",
      timestamp: "09:30:02",
      type: "SYSTEM",
      message: "Market Open Bell Volatility Flush active. Guardrails armed.",
    },
    {
      id: "2",
      timestamp: "09:35:10",
      type: "ORDER",
      symbol: "NVDA",
      message: "ORB 5-min breakout detected. BUY 150 NVDA filled @ $124.50",
      side: "BUY",
      qty: 150,
      price: 124.5,
    },
    {
      id: "3",
      timestamp: "09:41:35",
      type: "BRACKET",
      symbol: "NVDA",
      message: "Trailing stop advanced to $123.75 (locking risk buffer)",
      price: 123.75,
    },
  ],
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

    let resolvedWsUrl = wsUrl;
    if (isBrowser && (wsUrl === "ws://127.0.0.1:8005/ws/ui" || !wsUrl)) {
      resolvedWsUrl = `${wsProto}//${hostname}:8005/ws/ui`;
    }

    let httpBase = `${httpProto}//${hostname}:8005`;
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
                  shares: first.shares || first.qty || 100,
                  entry_price: first.avg_entry_price || first.entry_price || 0,
                  market_price: first.market_price || 0,
                  market_value: first.market_value || 0,
                  unrealized_pnl: first.unrealized_pnl || 0,
                  unrealized_pnl_pct: first.unrealized_pnl_pct || 0,
                  stop_loss: first.stop_loss,
                  take_profit_1: first.take_profit_1,
                  take_profit_2: first.take_profit_2,
                  strategy_id: first.strategy_id || "ORB",
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
                all_positions: payload.all_positions || prev.all_positions,
                positions_count: payload.positions_count ?? (primary ? 1 : 0),
                working_orders_count: payload.working_orders_count ?? prev.working_orders_count,
                recent_activity: payload.recent_activity || prev.recent_activity,
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
    }
  }, [getResolvedEndpoints]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    // Fallback: poll backend health & audit if available
    const pollInterval = setInterval(async () => {
      if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
        try {
          const { httpBase } = getResolvedEndpoints();
          const res = await fetch(`${httpBase}/api/audit?limit=10`);
          if (res.ok) {
            const logs = await res.json();
            if (Array.isArray(logs) && logs.length > 0) {
              setState((prev) => ({
                ...prev,
                recent_activity: logs.map((l: any, i: number) => ({
                  id: String(l.order_id || i),
                  timestamp: l.timestamp ? new Date(l.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString(),
                  type: l.event_type || "EXEC",
                  symbol: l.symbol,
                  message: `${l.side || ""} ${l.qty || ""} ${l.symbol || ""} - ${l.status || l.reason || "PROCESSED"}`,
                  price: l.price,
                  qty: l.qty,
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

  const sendAction = useCallback((payload: Record<string, any>) => {
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
    }
    return false;
  }, [getResolvedEndpoints]);

  const flattenPosition = useCallback((symbol: string) => {
    sendAction({ action: "FLATTEN_POSITION", symbol });
  }, [sendAction]);

  const flattenAll = useCallback(() => {
    sendAction({ action: "FLATTEN_ALL" });
  }, [sendAction]);

  const tightenStop = useCallback((symbol: string, newStop: number) => {
    sendAction({ action: "TIGHTEN_STOP", symbol, new_stop: newStop });
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
