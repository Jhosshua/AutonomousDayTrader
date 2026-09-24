import type { Metadata, Viewport } from "next";
// F15: @fontsource npm packages (work offline in `docker build` / `npm ci`), not next/font/google.
import "@fontsource/fraunces/500.css";
import "@fontsource/fraunces/600.css";
import "@fontsource/instrument-sans/400.css";
import "@fontsource/instrument-sans/500.css";
import "@fontsource/instrument-sans/600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Day Trader",
  description: "A practice trading account explained in plain English: what it's doing right now and why.",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Day Trader",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // F16: no maximumScale/userScalable lock — people must be able to pinch-zoom.
  viewportFit: "cover",
  themeColor: "#F7F3EC",
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
