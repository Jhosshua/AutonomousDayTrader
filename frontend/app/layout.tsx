import type { Metadata, Viewport } from "next";
// F15: @fontsource npm packages (work offline in `docker build` / `npm ci`), not next/font/google.
import "@fontsource/bricolage-grotesque/500.css";
import "@fontsource/bricolage-grotesque/600.css";
import "@fontsource/bricolage-grotesque/700.css";
import "@fontsource/bricolage-grotesque/800.css";
import "@fontsource/instrument-sans/400.css";
import "@fontsource/instrument-sans/500.css";
import "@fontsource/instrument-sans/600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cobalt Ledger",
  description: "Account performance, holdings, and detailed trading history in one operator dashboard.",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Cobalt Ledger",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // F16: no maximumScale/userScalable lock — people must be able to pinch-zoom.
  viewportFit: "cover",
  themeColor: "#2B4BFF",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-ground text-ink antialiased font-body">{children}</body>
    </html>
  );
}
