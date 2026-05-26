import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    // React 19 server components optimizations
    typedRoutes: false,
  },
  // Environment variable exposure to the browser
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env["NEXT_PUBLIC_API_BASE_URL"] ?? "http://localhost:8000",
  },
};

export default nextConfig;
