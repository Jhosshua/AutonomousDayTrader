import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutonomousDayTrader • Live Terminal",
  description: "Always-on algorithmic day trading terminal with fluid obsidian execution interface",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "DayTrader",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
  themeColor: "#000000",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark bg-black">
      <body className="min-h-screen bg-black text-white antialiased selection:bg-apple-purple/30 selection:text-white">
        {children}
      </body>
    </html>
  );
}
