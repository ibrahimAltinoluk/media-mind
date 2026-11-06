import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { isTauri } from "../api";

async function openExternal(url: string) {
  if (!/^https?:\/\//i.test(url)) return;
  if (isTauri) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else {
    window.open(url, "_blank", "noopener");
  }
}

const components: Components = {
  // Bağlantılar uygulama penceresinde değil, varsayılan tarayıcıda açılır
  a: ({ href, children }) => (
    <a href={href} onClick={(e) => { e.preventDefault(); if (href) openExternal(href); }}>{children}</a>
  ),
  // Geniş tablolar dar panelde yatay kayar
  table: ({ children }) => <div className="md-table"><table>{children}</table></div>,
  img: () => null,
};

/** Asistan yanıtları için Markdown (GFM: tablo, liste, kod). Ham HTML işlenmez. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
}
