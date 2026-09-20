"use client";

import { motion } from "framer-motion";
import { CheckCircle2, AlertCircle, ArrowDownRight, ArrowUpRight, Clock, ShieldAlert } from "lucide-react";
import { AuditRecord } from "@/types/trading";

interface ExecutionLogProps {
  records: AuditRecord[];
  maxItems?: number;
}

export default function ExecutionLog({ records, maxItems = 20 }: ExecutionLogProps) {
  const displayRecords = records.slice(0, maxItems);

  const getRecordIcon = (type: string) => {
    switch (type?.toUpperCase()) {
      case "ORDER":
      case "FILL":
      case "EXEC":
        return <CheckCircle2 className="w-4 h-4 text-apple-green" />;
      case "BRACKET":
      case "STOP":
        return <ArrowUpRight className="w-4 h-4 text-amber-400" />;
      case "CIRCUIT":
      case "ALERT":
      case "REJECT":
        return <ShieldAlert className="w-4 h-4 text-apple-red" />;
      default:
        return <Clock className="w-4 h-4 text-apple-blue" />;
    }
  };

  const getBadgeClass = (type: string) => {
    switch (type?.toUpperCase()) {
      case "ORDER":
      case "FILL":
        return "bg-apple-green/15 text-apple-green border-apple-green/30";
      case "BRACKET":
        return "bg-amber-500/15 text-amber-300 border-amber-500/30";
      case "CIRCUIT":
      case "REJECT":
        return "bg-apple-red/15 text-apple-red border-apple-red/30";
      default:
        return "bg-apple-blue/15 text-apple-blue border-apple-blue/30";
    }
  };

  return (
    <div className="rounded-3xl bg-white/[0.03] border border-white/[0.08] p-4 backdrop-blur-xl space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-neutral-400" />
          <h3 className="text-sm font-bold text-white tracking-tight">Execution Audit Log</h3>
        </div>
        <span className="text-[11px] text-neutral-400 num-tabular">
          {records.length} Events Logged
        </span>
      </div>

      {displayRecords.length === 0 ? (
        <div className="py-6 text-center text-xs text-neutral-500">
          No execution events recorded yet.
        </div>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto no-scrollbar pr-1">
          {displayRecords.map((item, idx) => (
            <motion.div
              key={item.id || idx}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2, delay: idx * 0.02 }}
              className="p-2.5 rounded-2xl bg-white/[0.02] hover:bg-white/[0.05] border border-white/[0.04] flex items-start gap-3 text-xs transition-colors"
            >
              <div className="mt-0.5 flex-shrink-0">{getRecordIcon(item.type)}</div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    {item.symbol && (
                      <span className="font-bold text-white text-[11px]">{item.symbol}</span>
                    )}
                    <span
                      className={`px-1.5 py-0.2 rounded-md border text-[9px] font-bold uppercase tracking-wider ${getBadgeClass(
                        item.type
                      )}`}
                    >
                      {item.type}
                    </span>
                  </div>
                  <span className="text-[10px] text-neutral-500 num-tabular">{item.timestamp}</span>
                </div>

                <p className="text-neutral-300 text-[11px] mt-0.5 leading-snug break-words">
                  {item.message}
                </p>

                {(item.price || item.qty) && (
                  <div className="flex items-center gap-3 mt-1 text-[10px] text-neutral-400 num-tabular">
                    {item.qty && <span>Qty: {item.qty}</span>}
                    {item.price && <span>Price: ${item.price.toFixed(2)}</span>}
                    {item.side && <span className="font-semibold text-white">{item.side}</span>}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  );
}
