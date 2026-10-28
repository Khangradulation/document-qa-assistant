/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// Content-Security-Policy only for production builds (dev server needs inline HMR scripts).
// connect-src allows only our own origin and AWS endpoints (API Gateway, Cognito, S3 presigned URLs).
const csp: Plugin = {
  name: "inject-csp",
  apply: "build",
  transformIndexHtml(html) {
    const policy = [
      "default-src 'self'",
      "script-src 'self'",
      "style-src 'self'",
      "img-src 'self' data:",
      "connect-src 'self' https://*.amazonaws.com",
      "frame-src 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; ");
    return html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${policy}" />`);
  },
};

export default defineConfig({
  // amazon-cognito-identity-js was written for Node-style bundlers and reads `global`; browsers only have `globalThis`.
  define: { global: "globalThis" },
  plugins: [react(), csp],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
