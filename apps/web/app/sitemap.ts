import type { MetadataRoute } from "next";

const BASE = "https://credit-ops-web.vercel.app";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${BASE}/`, priority: 1 },
    { url: `${BASE}/cong-viec`, priority: 0.6 },
    { url: `${BASE}/ho-so`, priority: 0.6 },
  ];
}
