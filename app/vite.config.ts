import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
// @ts-expect-error type error without @types/node package
import process from "node:process";
const host = process.env.TAURI_DEV_HOST;

/**
 * Eski macOS (ör. 2012 Mac'lerde Catalina) WebKit'i regex "lookbehind" sözdizimini tanımıyor (Safari 16.4 öncesi);
 * tek bir regex yüzünden tüm paket ayrıştırılamıyor ve uygulama beyaz sayfa açılıyordu.
 * remark-gfm'in e-posta bağlantısı regex'i eşdeğer, lookbehind'sız sürümle değiştirilir
 * (önceki karakter denetimini kütüphanenin findEmail işlevi zaten yapıyor).
 */
function oldWebkitRegex(): Plugin {
  const from = "/(?<=^|\\s|\\p{P}|\\p{S})([-.\\w+]+)@([-\\w]+(?:\\.[-\\w]+)+)/gu";
  const to = "/([-.\\w+]+)@([-\\w]+(?:\\.[-\\w]+)+)/gu";
  return {
    name: "old-webkit-regex",
    enforce: "pre",
    transform(code, id) {
      if (!id.includes("mdast-util-gfm-autolink-literal") || !code.includes(from)) return null;
      return { code: code.replace(from, to), map: null };
    },
  };
}

// https://vite.dev/config/
export default defineConfig(() => ({
  plugins: [oldWebkitRegex(), react()],

  // macOS 10.15 Catalina'daki WebKit (Safari 13–15) de çalıştırabilsin: yeni sözdizimi ve CSS iç içe kuralları düşürülür
  build: {
    target: ["es2019", "safari13"],
    cssTarget: ["safari13"],
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
}));
