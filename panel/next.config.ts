import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The FastAPI backend serves the browser at 127.0.0.1 in this sandbox
  // (see docs/panel.md); without this the dev server's HMR socket refuses
  // cross-origin requests from that host.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
