import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The FastAPI backend serves the browser at 127.0.0.1 in this sandbox
  // (see docs/panel.md); without this the dev server's HMR socket refuses
  // cross-origin requests from that host.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Docker (docker-compose.yml) runs the traced-files build this produces
  // instead of `next start`, which would otherwise need the full
  // node_modules tree copied into the image.
  output: "standalone",
};

export default nextConfig;
