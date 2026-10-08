import type { MetadataRoute } from "next";
export const dynamic = "force-static";
export default function manifest(): MetadataRoute.Manifest {
  return { name: "Cobalt Ledger", short_name: "Cobalt", start_url: "/", display: "minimal-ui",
    theme_color: "#2B4BFF", background_color: "#EEF1FA",
    icons: [{ src: "/icon.png", sizes: "32x32", type: "image/png" }, { src: "/apple-icon.png", sizes: "180x180", type: "image/png" }] };
}
