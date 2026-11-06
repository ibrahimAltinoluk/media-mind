import type React from "react";
import { useMemo, useRef, useState } from "react";
import type { LibraryFilter, LibraryView } from "../state";
import type { Album, Facets } from "../types";
import { cls, fmtInt } from "../util";
import { AlbumIcon, Camera, Clock, Close, Film, Heart, Image, People, Photos, Pin, Search, Sparkles } from "./Icons";
import { Popover } from "./Popover";

/**
 * Akıllı arama kutusu. Kullanıcı yazarken kişi, yer, tarih, etiket, albüm ve tür önerileri çıkar; seçilen öneri
 * kutunun içinde jeton olur. Özel sözdizimi ("kişi:") gerekmez: "Mihri Londra 2026 kumsal" yazıp Enter'a basınca
 * tanınan sözcükler kendiliğinden jetona dönüşür, kalanı serbest arama (anlam + metin) olarak kalır.
 * Birden çok kişi ya da etiket VE ile birleşir ("Ali Ayşe" → ikisinin birlikte olduğu fotoğraflar).
 */

type Kind = "person" | "place" | "region" | "year" | "month" | "date" | "tag" | "camera" | "album" | "view" | "similar";

interface Entity {
  kind: Kind;
  label: string;
  sub?: string;
  n?: number;
  /** Arama için katlanmış adlar (tam ad, ilk ad…) */
  keys: string[];
  apply: (f: LibraryFilter) => LibraryFilter;
  /** Bu süzgeç zaten etkin mi (öneride tekrar gösterilmez) */
  has?: (f: LibraryFilter) => boolean;
  view?: LibraryView;
}

const TR: Record<string, string> = { ç: "c", ğ: "g", ı: "i", ö: "o", ş: "s", ü: "u", â: "a", î: "i", û: "u" };
export const fold = (s: string) => s.toLocaleLowerCase("tr").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[çğıöşüâîû]/g, (c) => TR[c] ?? c).trim();

const MONTHS = ["ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik"];
const MONTH_NAMES = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
/** Türkçe ekler: "Londra'da", "Ali'nin", "İstanbulda", "Ayşeyle" */
const SUFFIXES = ["daki", "deki", "taki", "teki", "nin", "nın", "nun", "nün", "yla", "yle", "dan", "den", "tan", "ten", "da", "de", "ta", "te", "in", "ın", "un", "ün", "la", "le"].map(fold);
const STOP = new Set(["ve", "ile", "ya", "veya", "&", ",", "olan", "icinde", "fotograflari", "fotolari"]);

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function relativeDates(): Entity[] {
  const now = new Date();
  const y = now.getFullYear(), m = now.getMonth();
  const range = (label: string, keys: string[], from: Date, to: Date): Entity => ({
    kind: "date", label, keys: keys.map(fold),
    apply: (f) => ({ ...f, date_from: iso(from), date_to: iso(to), label, year: "", month: "" }), has: (f) => f.label === label && !!f.date_from,
  });
  const day = (offset: number) => { const d = new Date(now); d.setDate(d.getDate() + offset); return d; };
  const monday = day(-((now.getDay() + 6) % 7));
  return [
    range("Bugün", ["bugün"], now, now),
    range("Dün", ["dün"], day(-1), day(-1)),
    range("Bu hafta", ["bu hafta"], monday, now),
    range("Bu ay", ["bu ay"], new Date(y, m, 1), now),
    range("Geçen ay", ["geçen ay"], new Date(y, m - 1, 1), new Date(y, m, 0)),
    range("Son 30 gün", ["son 30 gün", "son bir ay"], day(-30), now),
    range("Bu yıl", ["bu yıl", "bu sene"], new Date(y, 0, 1), now),
    range("Geçen yıl", ["geçen yıl", "geçen sene"], new Date(y - 1, 0, 1), new Date(y - 1, 11, 31)),
    range("Bu yaz", ["bu yaz"], new Date(y, 5, 1), new Date(y, 7, 31)),
    range("Geçen yaz", ["geçen yaz"], new Date(y - 1, 5, 1), new Date(y - 1, 7, 31)),
  ];
}

const addList = (cur: string, v: string, sep: string) => (cur ? (cur.split(sep).includes(v) ? cur : `${cur}${sep}${v}`) : v);

function buildEntities(facets: Facets | undefined, albums: Album[] | undefined): Entity[] {
  const out: Entity[] = [];
  for (const p of facets?.people ?? []) {
    if (!p.name) continue;
    const parts = fold(p.name).split(/\s+/);
    out.push({ kind: "person", label: p.name, n: p.n, keys: [fold(p.name), ...(parts.length > 1 ? [parts[0]] : [])],
      apply: (f) => ({ ...f, person: addList(f.person, String(p.id), ",") }), has: (f) => f.person.split(",").includes(String(p.id)) });
  }
  const regions = new Map<string, number>();
  for (const p of facets?.places ?? []) {
    out.push({ kind: "place", label: p.place, sub: p.region && p.region !== p.place ? p.region : undefined, n: p.n, keys: [fold(p.place)],
      apply: (f) => ({ ...f, place: p.place, region: "" }), has: (f) => f.place === p.place });
    if (p.region) regions.set(p.region, (regions.get(p.region) ?? 0) + p.n);
  }
  for (const [r, n] of regions) {
    if ((facets?.places ?? []).some((p) => p.place === r && p.n === n)) continue; // tek yerli bölge: yer ile aynı
    out.push({ kind: "region", label: r, sub: "Bölgenin tamamı", n, keys: [fold(r)], apply: (f) => ({ ...f, region: r, place: "" }), has: (f) => f.region === r });
  }
  for (const y of facets?.years ?? []) {
    out.push({ kind: "year", label: y.year, n: y.n, keys: [y.year], apply: (f) => ({ ...f, year: y.year, date_from: "", date_to: "", label: "" }), has: (f) => f.year === y.year });
  }
  MONTHS.forEach((mk, i) => out.push({ kind: "month", label: MONTH_NAMES[i], sub: "Her yıl", keys: [mk],
    apply: (f) => ({ ...f, month: String(i + 1).padStart(2, "0"), date_from: "", date_to: "", label: "" }), has: (f) => f.month === String(i + 1).padStart(2, "0") }));
  out.push(...relativeDates());
  for (const t of facets?.tags ?? []) {
    out.push({ kind: "tag", label: t.tag, n: t.n, keys: [fold(t.tag)], apply: (f) => ({ ...f, tag: addList(f.tag, t.tag, "|") }), has: (f) => f.tag.split("|").includes(t.tag) });
  }
  for (const a of albums ?? []) {
    out.push({ kind: "album", label: a.name, n: a.count ?? undefined, keys: [fold(a.name)], view: "album",
      apply: (f) => ({ ...f, album: String(a.id), label: a.name }) });
  }
  for (const c of facets?.cameras ?? []) {
    out.push({ kind: "camera", label: c.camera, n: c.n, keys: [fold(c.camera)], apply: (f) => ({ ...f, camera: c.camera }), has: (f) => f.camera === c.camera });
  }
  const view = (label: string, v: LibraryView, keys: string[]): Entity => ({ kind: "view", label, keys: keys.map(fold), view: v, apply: (f) => f });
  out.push(view("Videolar", "video", ["video", "videolar", "videoları", "film"]), view("Fotoğraflar", "photo", ["fotoğraf", "fotoğraflar", "foto"]),
    view("Favoriler", "favorites", ["favori", "favoriler", "sevdiklerim"]));
  return out;
}

const ICON: Record<Kind, (p: { size?: number }) => React.ReactElement> = {
  person: People, place: Pin, region: Pin, year: Clock, month: Clock, date: Clock, tag: Sparkles, camera: Camera, album: AlbumIcon, view: Photos, similar: Image,
};
const GROUP: Record<Kind, string> = {
  person: "Kişiler", place: "Yerler", region: "Yerler", year: "Tarih", month: "Tarih", date: "Tarih", tag: "Fotoğraftakiler", album: "Albümler", camera: "Kameralar", view: "Tür", similar: "Benzer",
};
const ORDER: Kind[] = ["person", "place", "region", "date", "year", "month", "tag", "album", "view", "camera"];

/** Bir sözcük öbeğini tam eşleşen varlığa çevir (Türkçe ekleri de dener). Etiket ve kamera kendiliğinden jeton olmaz. */
function exact(index: Map<string, Entity[]>, phrase: string): Entity | null {
  // Ek yalnız öbeğin son sözcüğünden atılır ("Ali Yılmaz'ın" → "ali yilmaz"); kesme işareti sözcüğü bölmesin
  const words = phrase.split(" ");
  const last = words.pop() ?? "";
  const head = words.length ? words.join(" ") + " " : "";
  const tries = [phrase];
  if (last.includes("'")) tries.push(head + last.split("'")[0]);
  else for (const s of SUFFIXES) if (last.length > s.length + 2 && last.endsWith(s)) tries.push(head + last.slice(0, -s.length));
  for (const t of tries) {
    const hit = index.get(t)?.find((e) => e.kind !== "camera" && e.kind !== "album");
    if (hit) return hit;
  }
  return null;
}

interface Parsed { ents: Entity[]; rest: string }

function parse(index: Map<string, Entity[]>, text: string): Parsed {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const ents: Entity[] = [];
  const rest: string[] = [];
  for (let i = 0; i < words.length;) {
    let hit: Entity | null = null, len = 0;
    for (let n = Math.min(3, words.length - i); n >= 1 && !hit; n--) {
      const e = exact(index, fold(words.slice(i, i + n).join(" ").replace(/[“”"]/g, "")));
      if (e) { hit = e; len = n; }
    }
    if (hit) {
      if (!ents.some((x) => x.kind === hit!.kind && x.label === hit!.label)) ents.push(hit);
      i += len;
    } else { rest.push(words[i]); i++; }
  }
  const restText = ents.length ? rest.filter((w) => !STOP.has(fold(w))).join(" ") : rest.join(" ");
  return { ents, rest: restText };
}

export interface SearchToken { key: string; kind: Kind; label: string; remove: (f: LibraryFilter) => LibraryFilter }

export function SearchField({ q, setQ, filter, setFilter, view, setView, facets, albums, tokens, placeholder }: {
  q: string; setQ: (s: string) => void;
  filter: LibraryFilter; setFilter: (f: LibraryFilter) => void;
  view: LibraryView; setView: (v: LibraryView) => void;
  facets: Facets | undefined; albums: Album[] | undefined;
  tokens: SearchToken[]; placeholder: string;
}) {
  const box = useRef<HTMLLabelElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const ents = useMemo(() => buildEntities(facets, albums), [facets, albums]);
  const index = useMemo(() => {
    const m = new Map<string, Entity[]>();
    for (const e of ents) for (const k of e.keys) m.set(k, [...(m.get(k) ?? []), e]);
    return m;
  }, [ents]);

  const text = q.trim();
  const parsed = useMemo(() => (text ? parse(index, text) : { ents: [], rest: "" }), [index, text]);
  const smart = parsed.ents.length > 0 ? parsed : null;

  // Yazılan metnin (ya da son sözcüğün) önek eşleşmeleri
  const sugg = useMemo(() => {
    if (!text) return [] as Entity[];
    const whole = fold(text);
    const last = fold(text.split(/\s+/).pop() ?? "");
    const score = (e: Entity) => {
      let best = -1;
      for (const k of e.keys) {
        for (const needle of whole === last ? [whole] : [whole, last]) {
          if (needle.length < 1) continue;
          if (k === needle) best = Math.max(best, 3);
          else if (k.startsWith(needle)) best = Math.max(best, 2);
          else if (k.split(/\s+/).some((w) => w.startsWith(needle))) best = Math.max(best, 1.5);
          else if (needle.length >= 3 && k.includes(needle)) best = Math.max(best, 1);
          if (needle === last && needle !== whole) best = best > 0 ? best - 0.5 : best; // son sözcük eşleşmesi daha zayıf
        }
      }
      return best;
    };
    const scored = ents.map((e) => ({ e, s: score(e) })).filter((x) => x.s > 0)
      .filter(({ e }) => !(e.kind === "view" && view === e.view) && !e.has?.(filter));
    scored.sort((a, b) => b.s - a.s || (b.e.n ?? 0) - (a.e.n ?? 0));
    const per: Partial<Record<Kind, number>> = {};
    const out: Entity[] = [];
    for (const { e } of scored) {
      const g = GROUP[e.kind];
      const c = Object.entries(per).filter(([k]) => GROUP[k as Kind] === g).reduce((a, [, v]) => a + (v ?? 0), 0);
      if (c >= 4) continue;
      per[e.kind] = (per[e.kind] ?? 0) + 1;
      out.push(e);
      if (out.length >= 14) break;
    }
    return out.sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));
  }, [ents, text, view, filter]);

  // Satırlar: [akıllı birleşik öneri] + [“metni” ara] + öneriler
  type Row = { id: string; run: () => void };
  const rows: Row[] = [];
  const applyEnts = (es: Entity[], rest: string) => {
    let f = { ...filter };
    let v: LibraryView | null = null;
    for (const e of es) { f = e.apply(f); if (e.view) v = e.view; }
    f.q = rest;
    if (v) setView(v);
    if (v && v !== "album" && f.album) f = { ...f, album: "" };
    setQ(rest);
    setFilter(f);
    setHi(0);
    if (!rest) setOpen(false);
    input.current?.focus();
  };
  if (smart) rows.push({ id: "smart", run: () => applyEnts(smart.ents, smart.rest) });
  if (text) rows.push({ id: "text", run: () => { setFilter({ ...filter, q: text }); setOpen(false); } });
  sugg.forEach((e, i) => {
    // Önerinin yerini aldığı sözcük(ler) metinden çıkarılır
    const whole = fold(text);
    const usedWhole = e.keys.some((k) => k.startsWith(whole) || k.split(/\s+/).some((w) => w.startsWith(whole)) || (whole.length >= 3 && k.includes(whole)));
    const rest = usedWhole ? "" : text.split(/\s+/).slice(0, -1).join(" ");
    rows.push({ id: `s${i}`, run: () => applyEnts([e], rest) });
  });
  const active = Math.min(hi, Math.max(0, rows.length - 1));

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && rows.length) { setOpen(true); setHi((h) => Math.min(rows.length - 1, h + 1)); e.preventDefault(); }
    else if (e.key === "ArrowUp" && rows.length) { setHi((h) => Math.max(0, h - 1)); e.preventDefault(); }
    else if (e.key === "Enter") { if (open && rows[active]) rows[active].run(); else setFilter({ ...filter, q: text }); e.preventDefault(); }
    else if (e.key === "Escape") { if (open) setOpen(false); else if (q) { setQ(""); } e.stopPropagation(); }
    else if (e.key === "Backspace" && !q && tokens.length) { setFilter(tokens[tokens.length - 1].remove(filter)); e.preventDefault(); }
  };

  const Chip = ({ e }: { e: Entity }) => {
    const I = ICON[e.kind];
    return <span className={cls("stoken", `k-${e.kind}`)}><I size={11} />{e.label}</span>;
  };

  return (
    <>
      <label ref={box} className={cls("field search smart", (tokens.length > 0 || open) && "wide")} onMouseDown={(e) => { if (e.target === box.current) { e.preventDefault(); input.current?.focus(); } }}>
        <Search size={14} style={{ color: "var(--muted)", flex: "none" }} />
        <span className="sr">Ara</span>
        <span className="stokens">
          {tokens.map((t) => {
            const I = ICON[t.kind];
            return (
              <span key={t.key} className={cls("stoken", `k-${t.kind}`)}>
                <I size={11} />{t.label}
                <button type="button" className="x" onMouseDown={(e) => e.preventDefault()} onClick={() => setFilter(t.remove(filter))} aria-label={`${t.label} süzgecini kaldır`}><Close size={9} /></button>
              </span>
            );
          })}
          <input ref={input} type="search" placeholder={tokens.length ? "" : placeholder} value={q} aria-autocomplete="list" aria-expanded={open && rows.length > 0}
            onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0); }} onFocus={() => text && setOpen(true)} onBlur={() => setOpen(false)} onKeyDown={onKey} />
        </span>
      </label>
      {open && rows.length > 0 && (
        <Popover anchor={box.current} onClose={() => { if (document.activeElement !== input.current) setOpen(false); }} width={380} align="end">
          {/* Öneriye tıklamak odağı kutudan almasın (Popover kaydırma/dış tıklamada kapanır; odak kutudayken açık kalır) */}
          <div className="ssugg" role="listbox" aria-label="Arama önerileri" onMouseDown={(e) => e.preventDefault()}>
            {smart && (
              <button role="option" aria-selected={active === 0} className={cls("srow smartrow", active === 0 && "hi")} onMouseEnter={() => setHi(0)} onClick={rows[0].run}>
                <Sparkles size={14} />
                <span className="grow stokens-inline">
                  {smart.ents.map((e) => <Chip key={e.kind + e.label} e={e} />)}
                  {smart.rest && <span className="muted">+ “{smart.rest}”</span>}
                </span>
                <kbd>↩</kbd>
              </button>
            )}
            {text && (() => {
              const i = smart ? 1 : 0;
              return (
                <button role="option" aria-selected={active === i} className={cls("srow", active === i && "hi")} onMouseEnter={() => setHi(i)} onClick={rows[i].run}>
                  <Search size={14} /><span className="grow ellipsis">“{text}” için ara</span>{!smart && <kbd>↩</kbd>}
                </button>
              );
            })()}
            {sugg.map((e, i) => {
              const ri = i + (smart ? 1 : 0) + (text ? 1 : 0);
              const head = i === 0 || GROUP[sugg[i - 1].kind] !== GROUP[e.kind];
              const I = e.kind === "view" ? (e.view === "video" ? Film : e.view === "favorites" ? Heart : Photos) : ICON[e.kind];
              return (
                <div key={e.kind + e.label}>
                  {head && <div className="sgroup">{GROUP[e.kind]}</div>}
                  <button role="option" aria-selected={active === ri} className={cls("srow", active === ri && "hi")} onMouseEnter={() => setHi(ri)} onClick={rows[ri].run}>
                    <I size={14} />
                    <span className="grow ellipsis">{e.label}{e.sub && <span className="muted"> · {e.sub}</span>}</span>
                    {e.n != null && <span className="muted num">{fmtInt(e.n)}</span>}
                  </button>
                </div>
              );
            })}
            <div className="shint">Kişi, yer, tarih ya da fotoğraftaki bir şeyi yaz; birden çok seçebilirsin. ⌫ son süzgeci kaldırır.</div>
          </div>
        </Popover>
      )}
    </>
  );
}
