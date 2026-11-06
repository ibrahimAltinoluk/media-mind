import { useEffect, useRef, useState } from "react";

export interface ScrubMark { offset: number; year: string; label: string }

/**
 * Fotoğraflar uygulamasındaki gibi zaman çizelgesi: sağ kenarda yıllar; üzerinde gezinince ay görünür,
 * tıklayınca ya da sürükleyince o aya atlanır. offset: ızgaranın başına göre piksel konumu.
 */
export function Scrubber({ marks, total, scrollEl, margin }: { marks: ScrubMark[]; total: number; scrollEl: HTMLElement | null; margin: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(0);
  const [hover, setHover] = useState<{ y: number; label: string } | null>(null);
  const [pos, setPos] = useState(0);
  const drag = useRef(false);

  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(() => setH(ref.current?.clientHeight ?? 0));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (!scrollEl) return;
    const on = () => setPos(Math.max(0, scrollEl.scrollTop - margin));
    on();
    scrollEl.addEventListener("scroll", on, { passive: true });
    return () => scrollEl.removeEventListener("scroll", on);
  }, [scrollEl, margin]);

  if (marks.length < 2 || total <= 0 || !h) return <div ref={ref} className="scrubber" aria-hidden="true" />;
  const toY = (o: number) => (o / total) * h;
  const markAt = (y: number) => {
    const o = (y / h) * total;
    let m = marks[0];
    for (const k of marks) {
      if (k.offset <= o) m = k;
      else break;
    }
    return m;
  };
  const go = (y: number) => {
    const m = markAt(y);
    scrollEl?.scrollTo({ top: margin + m.offset });
  };
  const yOf = (e: React.PointerEvent) => Math.max(0, Math.min(h, e.clientY - ref.current!.getBoundingClientRect().top));
  // Yıllar: her yılın ilk ayı; birbirine çok yakın etiketler gizlenir
  const years: { y: number; year: string }[] = [];
  for (const m of marks) {
    if (years.length && years[years.length - 1].year === m.year) continue;
    const y = toY(m.offset);
    if (years.length && y - years[years.length - 1].y < 16) continue;
    years.push({ y, year: m.year });
  }
  return (
    <div ref={ref} className="scrubber" role="slider" aria-label="Zaman çizelgesi" aria-valuetext={markAt(toY(pos)).label}
      onPointerDown={(e) => { drag.current = true; ref.current!.setPointerCapture(e.pointerId); go(yOf(e)); }}
      onPointerMove={(e) => { const y = yOf(e); setHover({ y, label: markAt(y).label }); if (drag.current) go(y); }}
      onPointerUp={() => { drag.current = false; }}
      onPointerLeave={() => { if (!drag.current) setHover(null); }}>
      {years.map((y) => <span key={y.year} className="scrub-year" style={{ top: y.y }}>{y.year}</span>)}
      <span className="scrub-pos" style={{ top: Math.min(h - 2, toY(pos)) }} />
      {hover && <span className="scrub-tip" style={{ top: hover.y }}>{hover.label}</span>}
    </div>
  );
}
