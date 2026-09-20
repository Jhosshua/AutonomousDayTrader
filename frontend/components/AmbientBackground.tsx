"use client";

import { useMemo } from "react";
import { motion } from "framer-motion";

interface AmbientBackgroundProps {
  dailyPnl: number;
  isCircuitBroken?: boolean;
}

export default function AmbientBackground({
  dailyPnl,
  isCircuitBroken = false,
}: AmbientBackgroundProps) {
  const glow = useMemo(() => {
    if (isCircuitBroken) {
      return {
        primary: "rgba(255, 69, 58, 0.42)",
        secondary: "rgba(180, 20, 20, 0.32)",
        accent: "rgba(255, 159, 10, 0.2)",
        intensity: 0.45,
      };
    }
    if (dailyPnl > 500) {
      // High profit momentum
      return {
        primary: "rgba(48, 209, 88, 0.35)",
        secondary: "rgba(100, 210, 255, 0.28)",
        accent: "rgba(50, 215, 150, 0.2)",
        intensity: 0.38,
      };
    }
    if (dailyPnl > 0) {
      // Moderate profit
      return {
        primary: "rgba(48, 209, 88, 0.22)",
        secondary: "rgba(94, 92, 230, 0.18)",
        accent: "rgba(10, 132, 255, 0.15)",
        intensity: 0.28,
      };
    }
    if (dailyPnl < -500) {
      // Severe drawdown
      return {
        primary: "rgba(255, 69, 58, 0.36)",
        secondary: "rgba(255, 159, 10, 0.26)",
        accent: "rgba(180, 20, 20, 0.25)",
        intensity: 0.4,
      };
    }
    if (dailyPnl < 0) {
      // Mild drawdown
      return {
        primary: "rgba(255, 69, 58, 0.22)",
        secondary: "rgba(94, 92, 230, 0.15)",
        accent: "rgba(255, 69, 58, 0.1)",
        intensity: 0.24,
      };
    }
    // Neutral standby
    return {
      primary: "rgba(94, 92, 230, 0.22)",
      secondary: "rgba(10, 132, 255, 0.18)",
      accent: "rgba(94, 92, 230, 0.12)",
      intensity: 0.2,
    };
  }, [dailyPnl, isCircuitBroken]);

  return (
    <motion.div
      aria-hidden="true"
      className="fixed inset-0 pointer-events-none -z-10 overflow-hidden bg-black"
      initial={false}
      animate={{ opacity: glow.intensity }}
      transition={{ duration: 1.2, ease: "easeInOut" }}
    >
      {/* Top Left Orb */}
      <motion.div
        className="absolute -top-24 -left-24 w-[380px] h-[380px] rounded-full blur-[100px] will-change-transform"
        animate={{
          background: `radial-gradient(circle, ${glow.primary} 0%, transparent 70%)`,
          scale: [1, 1.08, 1],
          x: [0, 15, 0],
          y: [0, -10, 0],
        }}
        transition={{
          duration: 8,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />

      {/* Top Right Orb */}
      <motion.div
        className="absolute top-12 -right-28 w-[340px] h-[340px] rounded-full blur-[110px] will-change-transform"
        animate={{
          background: `radial-gradient(circle, ${glow.secondary} 0%, transparent 70%)`,
          scale: [1.05, 0.95, 1.05],
          x: [0, -20, 0],
          y: [0, 15, 0],
        }}
        transition={{
          duration: 10,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />

      {/* Bottom Center Flow Orb */}
      <motion.div
        className="absolute bottom-10 left-1/4 w-[420px] h-[420px] rounded-full blur-[130px] will-change-transform"
        animate={{
          background: `radial-gradient(circle, ${glow.accent} 0%, transparent 70%)`,
          scale: [0.95, 1.05, 0.95],
        }}
        transition={{
          duration: 12,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />

      {/* Deep Obsidian vignette overlay */}
      <div className="absolute inset-0 bg-gradient-to-b from-black/40 via-transparent to-black/80" />
    </motion.div>
  );
}
