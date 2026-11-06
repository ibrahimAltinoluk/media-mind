import { useEffect, useState } from "react";
import { subscribeBusy, type BusyOp } from "../api";

/**
 * Bir tıklamanın sonucu beklenirken (yazma isteği 350 ms'den uzun sürerse) altta ne yapıldığını gösterir.
 * Uzarsa kullanıcıya tekrar tıklamasına gerek olmadığını söyler. Aynı istek zaten ikinci kez gönderilmez.
 */
export function BusyPill() {
  const [ops, setOps] = useState<BusyOp[]>([]);
  const [now, setNow] = useState(Date.now());
  useEffect(() => subscribeBusy(setOps), []);
  useEffect(() => {
    if (!ops.length) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [ops.length]);
  const shown = ops.filter((o) => now - o.started > 350);
  useEffect(() => {
    document.documentElement.classList.toggle("busy", shown.length > 0);
  }, [shown.length]);
  if (!shown.length) return null;
  const first = shown[0];
  const long = now - first.started > 5000;
  return (
    <div className="busy-pill" role="status" aria-live="polite">
      <span className="spin" />
      <span>
        <b>{first.label}…</b>
        {shown.length > 1 && <span className="muted"> +{shown.length - 1}</span>}
        {long && <span className="muted"> Sürüyor; tekrar tıklamana gerek yok.</span>}
      </span>
    </div>
  );
}
