import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Bir öğeye bağlı açılır menü. Sayfanın en üst katmanında (portal) açılır; kaydırma alanlarında kesilmez,
 * ekran kenarlarına göre kendini yerleştirir, dışarı tıklama / Escape / kaydırma ile kapanır.
 */
export function Popover({ anchor, onClose, width = 230, align = "end", children }: {
  anchor: HTMLElement | null;
  onClose: () => void;
  width?: number;
  align?: "start" | "end";
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!anchor) return;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const h = ref.current?.offsetHeight ?? 0;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let left = align === "end" ? r.right - width : r.left;
      left = Math.max(8, Math.min(left, vw - width - 8));
      let top = r.bottom + 4;
      if (h && top + h > vh - 8) top = Math.max(8, r.top - h - 4); // sığmazsa yukarı aç
      setPos((p) => (p && p.top === top && p.left === left ? p : { top, left }));
    };
    place();
    // İçerik değişip boyutu değişince (ör. birleştirme listesine geçiş) yeniden yerleştir
    const ro = new ResizeObserver(place);
    if (ref.current) ro.observe(ref.current);
    return () => ro.disconnect();
  }, [anchor, width, align]);

  useEffect(() => {
    const down = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t) || anchor?.contains(t)) return;
      onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const scroll = (e: Event) => {
      if (ref.current?.contains(e.target as Node)) return;
      onClose();
    };
    window.addEventListener("mousedown", down);
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      window.removeEventListener("mousedown", down);
      window.removeEventListener("keydown", key);
      window.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [anchor, onClose]);

  return createPortal(
    <div ref={ref} className="menu" role="menu"
      style={{ position: "fixed", top: pos?.top ?? -9999, left: pos?.left ?? -9999, width, zIndex: 80, maxHeight: "min(420px, calc(100vh - 16px))", overflowY: "auto" }}>
      {children}
    </div>,
    document.body,
  );
}
