import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// For local development, proxy API calls to a deployed stack:
//   VITE_API_PROXY=https://xxxx.cloudfront.net npm run dev
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: process.env.VITE_API_PROXY
      ? { "/api": { target: process.env.VITE_API_PROXY, changeOrigin: true } }
      : undefined,
  },
});
