"use client";

/** Shared with useTradingStream.ts's own resolution logic: when the static export is served
 * from the Next dev server (port 3005) the API lives on 8005; otherwise (Railway, or FastAPI
 * serving the export directly) same-origin is correct. */
export function apiBase(): string {
  if (typeof window === "undefined") return "";
  if (window.location.port === "3005") {
    return `${window.location.protocol}//${window.location.hostname}:8005`;
  }
  return "";
}
