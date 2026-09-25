import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A self-contained server for frontend/Dockerfile (.next/standalone). Traced from
  // the repository root: npm workspaces hoist dependencies there.
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, ".."),
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
