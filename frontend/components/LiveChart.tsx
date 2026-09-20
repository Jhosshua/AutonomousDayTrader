"use client";

import { useMemo, useState } from "react";
import { Position } from "@/types/trading";

interface LiveChartProps {
  position: Position;
  height?: number;
}

interface CandleData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export default function LiveChart({ position, height = 240 }: LiveChartProps) {
  const [hoveredCandle, setHoveredCandle] = useState<CandleData | null>(null);

  // Generate realistic intraday bars around entry and current market price if position.chart_points is absent
  const candles: CandleData[] = useMemo(() => {
    if (position.chart_points && position.chart_points.length > 0) {
      return position.chart_points.map((p) => ({
        time: typeof p.time === "number" ? new Date(p.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : String(p.time),
        open: p.open,
        high: p.high,
        low: p.low,
        close: p.close,
        volume: p.volume,
      }));
    }

    // Deterministic synthetic intraday progression for visualization
    const count = 18;
    const baseEntry = position.entry_price || 124.5;
    const targetClose = position.market_price || 126.8;
    const diff = targetClose - baseEntry;
    const generated: CandleData[] = [];

    let current = baseEntry - diff * 0.2;
    const startTime = new Date();
    startTime.setMinutes(startTime.getMinutes() - count * 2);

    for (let i = 0; i < count; i++) {
      const progress = i / (count - 1);
      const trend = baseEntry + diff * progress;
      const noise = (Math.sin(i * 1.3) * 0.35 + Math.cos(i * 0.7) * 0.2) * (diff ? Math.abs(diff) * 0.4 : 0.5);
      const open = i === 0 ? current : generated[i - 1].close;
      const close = i === count - 1 ? targetClose : trend + noise;
      const high = Math.max(open, close) + 0.25 + Math.abs(noise) * 0.3;
      const low = Math.min(open, close) - 0.2 - Math.abs(noise) * 0.2;

      const barTime = new Date(startTime.getTime() + i * 2 * 60000);
      generated.push({
        time: barTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        open: Number(open.toFixed(2)),
        high: Number(high.toFixed(2)),
        low: Number(low.toFixed(2)),
        close: Number(close.toFixed(2)),
        volume: Math.floor(50000 + Math.random() * 120000),
      });
    }

    return generated;
  }, [position.entry_price, position.market_price, position.chart_points]);

  // Price bounds calculation including bracket levels
  const { minPrice, maxPrice, priceRange } = useMemo(() => {
    const allPrices: number[] = [];
    candles.forEach((c) => {
      allPrices.push(c.high, c.low);
    });
    if (position.entry_price) allPrices.push(position.entry_price);
    if (position.market_price) allPrices.push(position.market_price);
    if (position.stop_loss) allPrices.push(position.stop_loss);
    if (position.take_profit_1) allPrices.push(position.take_profit_1);
    if (position.take_profit_2) allPrices.push(position.take_profit_2);

    const min = Math.min(...allPrices);
    const max = Math.max(...allPrices);
    const padding = (max - min) * 0.15 || 1.0;
    return {
      minPrice: min - padding,
      maxPrice: max + padding,
      priceRange: max - min + padding * 2,
    };
  }, [candles, position]);

  const getY = (price: number) => {
    if (priceRange === 0) return height / 2;
    return height - ((price - minPrice) / priceRange) * height;
  };

  const chartWidth = 500;
  const candleWidth = (chartWidth - 60) / Math.max(candles.length, 1);

  return (
    <div className="w-full rounded-2xl bg-black/50 border border-white/[0.08] p-3 backdrop-blur-xl relative overflow-hidden">
      {/* Chart Top Info Bar */}
      <div className="flex items-center justify-between text-xs mb-2 text-neutral-400">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white tracking-wide">{position.symbol}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-neutral-300">
            1-Min Real-Time
          </span>
        </div>
        <div className="num-tabular text-[11px]">
          {hoveredCandle ? (
            <span className="text-white">
              O: {hoveredCandle.open} H: {hoveredCandle.high} L: {hoveredCandle.low} C:{" "}
              {hoveredCandle.close}
            </span>
          ) : (
            <span>Live Price: <strong className="text-white">${position.market_price.toFixed(2)}</strong></span>
          )}
        </div>
      </div>

      {/* SVG Chart Area */}
      <div className="relative w-full overflow-hidden" style={{ height: `${height}px` }}>
        <svg
          viewBox={`0 0 ${chartWidth} ${height}`}
          className="w-full h-full overflow-visible"
          preserveAspectRatio="none"
        >
          {/* Subtle Grid Lines */}
          {[0.2, 0.4, 0.6, 0.8].map((ratio, i) => (
            <line
              key={i}
              x1="0"
              y1={height * ratio}
              x2={chartWidth}
              y2={height * ratio}
              stroke="rgba(255, 255, 255, 0.05)"
              strokeDasharray="2 4"
            />
          ))}

          {/* Horizontal Level: Take Profit 2 (Emerald) */}
          {position.take_profit_2 && (
            <g>
              <line
                x1="0"
                y1={getY(position.take_profit_2)}
                x2={chartWidth - 50}
                y2={getY(position.take_profit_2)}
                stroke="#30d158"
                strokeWidth="1"
                strokeDasharray="4 4"
                opacity="0.85"
              />
              <text
                x={chartWidth - 45}
                y={getY(position.take_profit_2) + 3}
                fill="#30d158"
                fontSize="9"
                fontWeight="bold"
                className="num-tabular"
              >
                TP2 ${position.take_profit_2.toFixed(2)}
              </text>
            </g>
          )}

          {/* Horizontal Level: Take Profit 1 (Green) */}
          {position.take_profit_1 && (
            <g>
              <line
                x1="0"
                y1={getY(position.take_profit_1)}
                x2={chartWidth - 50}
                y2={getY(position.take_profit_1)}
                stroke="#34c759"
                strokeWidth="1.2"
                strokeDasharray="4 3"
                opacity="0.9"
              />
              <text
                x={chartWidth - 45}
                y={getY(position.take_profit_1) + 3}
                fill="#34c759"
                fontSize="9"
                fontWeight="bold"
                className="num-tabular"
              >
                TP1 ${position.take_profit_1.toFixed(2)}
              </text>
            </g>
          )}

          {/* Horizontal Level: Entry Price (Cyan) */}
          {position.entry_price && (
            <g>
              <line
                x1="0"
                y1={getY(position.entry_price)}
                x2={chartWidth - 50}
                y2={getY(position.entry_price)}
                stroke="#64d2ff"
                strokeWidth="1.2"
                opacity="0.75"
              />
              <text
                x={chartWidth - 45}
                y={getY(position.entry_price) + 3}
                fill="#64d2ff"
                fontSize="9"
                fontWeight="bold"
                className="num-tabular"
              >
                ENT ${position.entry_price.toFixed(2)}
              </text>
            </g>
          )}

          {/* Horizontal Level: Stop Loss (Red) */}
          {position.stop_loss && (
            <g>
              <line
                x1="0"
                y1={getY(position.stop_loss)}
                x2={chartWidth - 50}
                y2={getY(position.stop_loss)}
                stroke="#ff453a"
                strokeWidth="1.2"
                strokeDasharray="4 3"
                opacity="0.9"
              />
              <text
                x={chartWidth - 45}
                y={getY(position.stop_loss) + 3}
                fill="#ff453a"
                fontSize="9"
                fontWeight="bold"
                className="num-tabular"
              >
                STP ${position.stop_loss.toFixed(2)}
              </text>
            </g>
          )}

          {/* Candlesticks */}
          {candles.map((candle, idx) => {
            const x = idx * candleWidth + candleWidth / 2;
            const isBull = candle.close >= candle.open;
            const color = isBull ? "#30d158" : "#ff453a";
            const bodyTop = getY(Math.max(candle.open, candle.close));
            const bodyHeight = Math.max(Math.abs(getY(candle.open) - getY(candle.close)), 2);
            const highY = getY(candle.high);
            const lowY = getY(candle.low);

            return (
              <g
                key={idx}
                onMouseEnter={() => setHoveredCandle(candle)}
                onMouseLeave={() => setHoveredCandle(null)}
                className="cursor-pointer"
              >
                {/* Wick */}
                <line
                  x1={x}
                  y1={highY}
                  x2={x}
                  y2={lowY}
                  stroke={color}
                  strokeWidth="1.2"
                  opacity="0.8"
                />
                {/* Body */}
                <rect
                  x={x - (candleWidth * 0.65) / 2}
                  y={bodyTop}
                  width={Math.max(candleWidth * 0.65, 3)}
                  height={bodyHeight}
                  fill={color}
                  rx="1"
                />
              </g>
            );
          })}

          {/* Current Market Price Laser Line & Pulse */}
          <line
            x1="0"
            y1={getY(position.market_price)}
            x2={chartWidth - 50}
            y2={getY(position.market_price)}
            stroke="#ffffff"
            strokeWidth="1.2"
            strokeDasharray="2 2"
            opacity="0.8"
          />
          <circle
            cx={chartWidth - 50}
            cy={getY(position.market_price)}
            r="3.5"
            fill="#ffffff"
            className="animate-ping"
            opacity="0.75"
          />
          <circle
            cx={chartWidth - 50}
            cy={getY(position.market_price)}
            r="2.5"
            fill="#ffffff"
          />
        </svg>
      </div>

      {/* Bracket Legend Footer */}
      <div className="flex items-center justify-between pt-2 mt-2 border-t border-white/[0.06] text-[10px] text-neutral-400">
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-apple-red rounded-full inline-block" /> Stop Loss
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-apple-teal rounded-full inline-block" /> Entry
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-apple-green rounded-full inline-block" /> Target 1 (1.5R)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-0.5 bg-emerald-400 rounded-full inline-block" /> Target 2 (2.5R)
        </span>
      </div>
    </div>
  );
}
