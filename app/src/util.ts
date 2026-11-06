export const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
export const AYLAR_KISA = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];

export const fmtInt = (n: number | null | undefined) => (n ?? 0).toLocaleString("tr-TR");

export function fmtBytes(n: number | null | undefined): string {
  let f = n ?? 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(f) >= 1024 && i < u.length - 1) {
    f /= 1024;
    i++;
  }
  return i === 0 ? `${f} B` : `${f.toLocaleString("tr-TR", { maximumFractionDigits: 1 })} ${u[i]}`;
}

/** İnsan dilinde süre tahmini: "yaklaşık 2 saat 10 dakika" */
export function fmtSpan(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const m = Math.round(seconds / 60);
  if (m < 1) return "1 dakikadan az";
  if (m < 60) return `${m} dakika`;
  const h = Math.floor(m / 60), r = m % 60;
  if (h >= 48) return `${Math.round(h / 24)} gün`;
  return r && h < 10 ? `${h} saat ${r} dakika` : `${h} saat`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "Tarih yok";
  const y = iso.slice(0, 4);
  const mo = Number(iso.slice(5, 7));
  const d = Number(iso.slice(8, 10));
  const t = iso.slice(11, 16);
  return `${d} ${AYLAR_KISA[mo - 1] ?? ""} ${y}${withTime && t ? `, ${t}` : ""}`;
}

export function monthLabel(key: string): string {
  if (key === "?") return "Tarihsiz";
  return `${AYLAR[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`;
}

export const basename = (p: string) => p.slice(p.lastIndexOf("/") + 1);
export const dirname = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

export function placeText(a: { place?: string | null; region?: string | null }): string | null {
  if (!a.place) return null;
  return a.region && a.region !== a.place ? `${a.place}, ${a.region}` : a.place;
}

const BROWSER_IMAGE = /^image\/(jpeg|png|webp|gif|heic|heif|avif)$/;
export const browserCanShow = (mime: string | null | undefined) => !!mime && BROWSER_IMAGE.test(mime);

export const SOURCE_LABEL: Record<string, string> = {
  exif: "EXIF",
  quicktime: "video meta verisi",
  filename: "dosya adından",
  mtime: "dosya tarihinden (tahmini)",
};

export const cls = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");
