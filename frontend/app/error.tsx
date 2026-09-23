"use client";

import { useEffect } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorBoundary({ error, reset }: ErrorProps) {
  useEffect(() => {
    // Log exception to client console for telemetry
    console.error("UI Uncaught Exception Boundary:", error);
  }, [error]);

  return (
    <div className="min-h-screen bg-[#000000] text-white flex items-center justify-center p-6 selection:bg-apple-purple/30">
      <div className="w-full max-w-md p-6 rounded-3xl bg-neutral-950/80 border border-white/10 backdrop-blur-xl shadow-2xl space-y-5 text-center">
        <div className="mx-auto w-12 h-12 rounded-2xl bg-apple-red/10 border border-apple-red/20 flex items-center justify-center text-apple-red">
          <AlertTriangle className="w-6 h-6" />
        </div>

        <div className="space-y-2">
          <h2 className="text-xl font-bold tracking-tight text-white">
            Client Telemetry Exception
          </h2>
          <p className="text-xs text-neutral-400 leading-relaxed">
            The trading interface encountered an unexpected rendering error. Your backend risk engines and working orders remain safe on the server.
          </p>
        </div>

        {error?.message && (
          <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] text-left">
            <span className="text-[10px] uppercase font-bold tracking-wider text-neutral-500 block mb-1">
              Error Details
            </span>
            <code className="text-xs text-apple-red font-mono break-all line-clamp-3 block">
              {error.message}
            </code>
          </div>
        )}

        <div className="pt-2">
          <button
            onClick={() => reset()}
            className="w-full py-3 px-4 rounded-2xl bg-white/10 hover:bg-white/20 active:scale-[0.98] text-white text-xs font-bold flex items-center justify-center gap-2 transition-all border border-white/10 shadow-lg"
          >
            <RotateCcw className="w-4 h-4 text-apple-green" />
            <span>Reload Stream & Recover State</span>
          </button>
        </div>
      </div>
    </div>
  );
}
