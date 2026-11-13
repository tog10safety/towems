import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const ROOT = path.dirname(fileURLToPath(import.meta.url));

// The dev server proxies /api to Django so the browser sees a single origin:
// no CORS preflights and the session cookie works exactly as in production.
const DJANGO_ORIGIN = process.env.VITE_DJANGO_ORIGIN || "http://127.0.0.1:8000";

// pdf.js needs its own font and character-map tables to draw a PDF whose fonts
// are not embedded (the MRM generator uses standard Helvetica/ZapfDingbats
// faces). Serve them from the installed package in development and copy them
// into the build, instead of committing a second copy to the repository.
const PDFJS_DIR = path.join(ROOT, "node_modules", "pdfjs-dist");
const PDFJS_ASSETS = ["standard_fonts", "cmaps"];

function pdfjsAssets() {
  return {
    name: "pdfjs-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/pdfjs/")) return next();
        const relative = decodeURIComponent(
          req.url.replace(/^\/pdfjs\//, "").split("?")[0]
        );
        const file = path.resolve(PDFJS_DIR, relative);
        if (!file.startsWith(PDFJS_DIR + path.sep) || !fs.existsSync(file)) return next();
        if (fs.statSync(file).isDirectory()) return next();
        res.setHeader("Content-Type", "application/octet-stream");
        fs.createReadStream(file).pipe(res);
      });
    },
    closeBundle() {
      for (const directory of PDFJS_ASSETS) {
        fs.cpSync(
          path.join(PDFJS_DIR, directory),
          path.join(ROOT, "dist", "pdfjs", directory),
          { recursive: true }
        );
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), pdfjsAssets()],
  server: {
    // Bind IPv4 loopback explicitly: the app is opened at
    // http://127.0.0.1:5173/, and Vite's default "localhost" resolves to
    // ::1 on this machine, which leaves 127.0.0.1 unanswered.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: DJANGO_ORIGIN,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
