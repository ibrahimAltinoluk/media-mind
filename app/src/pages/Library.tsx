import { useInfiniteQuery, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, isTauri, thumbUrl } from "../api";
import { DetailPanel, useTrashFlow } from "../components/DetailPanel";
import { AlbumAddMenu, CompressDialog, DateDialog, ExportDialog, LocationDialog } from "../components/EditDialogs";
import { Close, Filter, Folder, Heart, Image, Info, Left, Play, Right, Search } from "../components/Icons";
import { revealInFinder } from "../components/Sidebar";
import { Popover } from "../components/Popover";
import { Scrubber, type ScrubMark } from "../components/Scrubber";
import { Toolbar } from "../components/Toolbar";
import { EMPTY_FILTER, useApp, useSummary, type LibraryFilter, type LibraryView } from "../state";
import type { AssetItem, FolderEntry } from "../types";
import { cls, fmtDuration, fmtInt, monthLabel, placeText } from "../util";
import { SearchField, type SearchToken } from "../components/SearchField";

const MONTH_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const monthName = (m: string) => `${MONTH_TR[Number(m) - 1] ?? m} (her yıl)`;

const PAGE = 3000; // arama sonuçları (sunucuda zaten bütün hesaplanır)
const SPAGE = 400; // göz atarken: ızgara iskeleti tek seferde, ayrıntılar görünür oldukça bu kadarlık sayfalarla
const GAP = 2;
const PAD = 16;

const VIEW_TITLE: Record<LibraryView, string> = {
  all: "Kütüphane", photo: "Kütüphane", video: "Videolar", untracked: "Yeni eklenenler", favorites: "Favoriler", folders: "Klasörler", album: "Albüm"
};

const SIZES = [96, 130, 170, 230, 320];
const IMG_RE = /\.(jpe?g|png|heic|heif|webp|tiff?|gif|bmp|avif)$/i;

type Row = { type: "header"; key: string; title: string; sub: string; count: number; month?: string } | { type: "tiles"; key: string; start: number; end: number };
type Run = { k: string; n: number; p?: string };

const Tile = memo(function Tile({ repoId, item, index, size, selected, onSelect, onOpen, onMenu }: {
  repoId: string; item: AssetItem; index: number; size: number; selected: boolean;
  onSelect: (i: number, e: React.MouseEvent) => void; onOpen: (i: number) => void; onMenu: (i: number, e: React.MouseEvent) => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <button className={cls("tile", selected && "sel")} style={{ width: size, height: size }}
      onClick={(e) => onSelect(index, e)} onDoubleClick={() => onOpen(index)} onContextMenu={(e) => { e.preventDefault(); onMenu(index, e); }}
      aria-label={item.rel_path} aria-pressed={selected} title={item.rel_path}>
      {failed ? <span className="ph"><Image size={24} /></span> : (
        <img src={thumbUrl(repoId, item.blake3, size > 260 ? "m" : "s")} loading="lazy" decoding="async" alt="" draggable={false} onError={() => setFailed(true)} />
      )}
      {item.kind === "video" && <span className="badge b-vid"><Play size={10} />{fmtDuration(item.duration_ms)}</span>}
      {!item.tracked && <span className="badge b-new">Yeni</span>}
      {!!item.favorite && <span className="badge b-fav"><Heart size={13} filled /></span>}
    </button>
  );
});

function FolderTile({ repoId, f, selected, onSelect, onOpen, onMenu }: {
  repoId: string; f: FolderEntry; selected: boolean; onSelect: () => void; onOpen: () => void; onMenu: (el: HTMLElement) => void;
}) {
  return (
    <button className={cls("ftile", selected && "sel")} onClick={onSelect} onDoubleClick={onOpen}
      onContextMenu={(e) => { e.preventDefault(); onSelect(); onMenu(e.currentTarget); }}
      title={`${f.path}\n${fmtInt(f.count)} öğe`} aria-label={`${f.name} klasörü, ${f.count} öğe`}>
      <span className="fglyph">
        <span className="body">{f.covers[0] && <img src={thumbUrl(repoId, f.covers[0], "s")} alt="" loading="lazy" draggable={false} />}</span>
      </span>
      <span className="fname">{f.name}</span>
      <span className="fcount">{fmtInt(f.count)} öğe{f.new ? ` · ${fmtInt(f.new)} yeni` : ""}</span>
    </button>
  );
}

type FilterKey = "person" | "year" | "place" | "camera" | "tag";

function FilterMenu({ anchor, onClose, filter, setFilter, facets }: {
  anchor: HTMLElement | null; onClose: () => void; filter: LibraryFilter; setFilter: (f: LibraryFilter) => void;
  facets: ReturnType<typeof useFacets>["data"];
}) {
  const set = (k: FilterKey, v: string) => setFilter({ ...filter, [k]: v });
  const Sel = ({ k, label, opts }: { k: FilterKey; label: string; opts: { v: string; t: string }[] }) => (
    <div className="grow-row" style={{ minHeight: 36, padding: "4px 10px" }}>
      <span className="k">{label}</span>
      <select className="sel" style={{ maxWidth: 170 }} value={filter[k]} onChange={(e) => set(k, e.target.value)} aria-label={label}>
        <option value="">Tümü</option>
        {opts.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
      </select>
    </div>
  );
  return (
    <Popover anchor={anchor} onClose={onClose} width={300}>
      <div style={{ padding: "4px 0" }}>
        {!!facets?.people?.length && <Sel k="person" label="Kişi" opts={facets.people.map((p) => ({ v: String(p.id), t: `${p.name} (${fmtInt(p.n)})` }))} />}
        <Sel k="year" label="Yıl" opts={(facets?.years ?? []).map((y) => ({ v: y.year, t: `${y.year} (${fmtInt(y.n)})` }))} />
        <Sel k="place" label="Yer" opts={(facets?.places ?? []).map((p) => ({ v: p.place, t: `${placeText(p)} (${fmtInt(p.n)})` }))} />
        <Sel k="camera" label="Kamera" opts={(facets?.cameras ?? []).map((c) => ({ v: c.camera, t: `${c.camera} (${fmtInt(c.n)})` }))} />
        {!!facets?.tags?.length && <Sel k="tag" label="Etiket" opts={facets.tags.map((t) => ({ v: t.tag, t: `${t.tag} (${fmtInt(t.n)})` }))} />}
      </div>
    </Popover>
  );
}

function useFacets(repoId: string | null, scope: string) {
  return useQuery({ queryKey: ["facets", repoId, scope], queryFn: () => api.facets(repoId!, scope), enabled: !!repoId });
}

export function Library() {
  const { repoId, view, filter, setFilter, openViewer, toast, trackJob, setView, viewer, setSelection, showPlan, setPage, page } = useApp();
  const { data: s } = useSummary(repoId);
  const [q, setQ] = useState(filter.q);
  const [zoom, setZoom] = useState(() => Number(localStorage.getItem("mm.zoom") ?? 2));
  const [grouping, setGrouping] = useState<"month" | "folder">(view === "untracked" ? "folder" : "month");
  const [selected, setSelected] = useState<number | null>(null); // odak (bilgi paneli, klavye)
  const [selIds, setSelIds] = useState<Set<number>>(new Set()); // seçili dosyalar (⌘ / ⇧ ile çoklu)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<HTMLSpanElement | null>(null);
  const [menuMode, setMenuMode] = useState<"main" | "album">("main");
  const closeMenu = useCallback(() => { setMenu(null); setMenuMode("main"); }, []);
  const [dialog, setDialog] = useState<{ kind: "date" | "location" | "export" | "compress"; ids: number[]; sample?: string | null; bytes?: number } | null>(null);
  const closeDialog = useCallback(() => setDialog(null), []);
  const [saveSmart, setSaveSmart] = useState<string | null>(null);
  const albumsQ = useQuery({ queryKey: ["albums", repoId], queryFn: () => api.albums(repoId!), enabled: !!repoId });
  const trash = useTrashFlow();
  const qc = useQueryClient();
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [filterOpen, setFilterOpen] = useState(false);
  // Bilgi paneli seçimle açılıp kapanmaz; yalnız kullanıcı açar/kapatır (⌘I) ve tercih hatırlanır.
  // Böylece ızgara her tıklamada yeniden dizilmez.
  const [infoOpen, setInfoOpenRaw] = useState(() => { try { return localStorage.getItem("mm.info") === "1"; } catch { return false; } });
  const setInfoOpen = useCallback((v: boolean | ((x: boolean) => boolean)) => {
    setInfoOpenRaw((cur) => {
      const next = typeof v === "function" ? v(cur) : v;
      try { localStorage.setItem("mm.info", next ? "1" : "0"); } catch { /* yok */ }
      return next;
    });
  }, []);
  const folderMode = view === "folders";
  const [hist, setHist] = useState<{ back: string[]; fwd: string[] }>({ back: [], fwd: [] });
  const [selFolder, setSelFolder] = useState<string | null>(null);
  const [folderMenu, setFolderMenu] = useState<{ el: HTMLElement; f: FolderEntry } | null>(null);
  const closeFolderMenu = useCallback(() => setFolderMenu(null), []);
  const aboveRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const [margin, setMargin] = useState(0);
  const filterBtn = useRef<HTMLButtonElement>(null);
  const tile = SIZES[Math.max(0, Math.min(SIZES.length - 1, zoom))];

  useEffect(() => setGrouping(view === "untracked" ? "folder" : "month"), [view]);
  useEffect(() => setQ(filter.q), [filter.q]);
  useEffect(() => {
    const t = setTimeout(() => q !== filter.q && setFilter({ ...filter, q }), 300);
    return () => clearTimeout(t);
  }, [q, filter, setFilter]);
  useEffect(() => { try { localStorage.setItem("mm.zoom", String(zoom)); } catch { /* yok */ } }, [zoom]);

  const query = useMemo(() => ({
    scope: view === "untracked" ? "untracked" : folderMode ? "all" : "tracked",
    kind: view === "photo" || view === "video" ? view : undefined,
    favorite: view === "favorites" || undefined,
    search: filter.q.trim() || undefined,
    year: filter.year || undefined, place: filter.place || undefined,
    camera: filter.camera || undefined, folder: filter.folder || undefined,
    tag: filter.tag || undefined, person: filter.person || undefined,
    month: filter.month || undefined, region: filter.region || undefined, similar: filter.similar || undefined,
    direct: folderMode && !filter.q.trim() ? true : undefined,
    album: view === "album" && filter.album ? filter.album : undefined,
    date_from: filter.date_from || undefined, date_to: filter.date_to || undefined,
    sort: view === "untracked" || folderMode ? "path" : view === "album" || filter.date_from ? "taken_asc" : "taken_desc",
  }), [view, filter, folderMode]);

  const searching = !!query.search || !!query.similar;
  const layout = folderMode ? "none" : grouping;
  // Arama: sonuçlar sunucuda bütün hesaplandığı için hepsi yüklenir
  const searchQ = useInfiniteQuery({
    queryKey: ["assets", repoId, query, "search"],
    queryFn: ({ pageParam }) => api.assets(repoId!, { ...query, offset: pageParam, limit: PAGE }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
    enabled: !!repoId && searching,
  });
  useEffect(() => {
    if (searching && searchQ.hasNextPage && !searchQ.isFetchingNextPage) searchQ.fetchNextPage();
  }, [searching, searchQ]);
  // Göz atma: önce tüm sonucun iskeleti (kimlikler + ay/klasör dizileri), ayrıntılar görünür oldukça sayfa sayfa.
  // Böylece 100 bin fotoğraflık kütüphane de hemen açılır, bellekte yalnız bakılan kısım durur.
  const indexQ = useQuery({
    queryKey: ["assets", repoId, query, "index", layout],
    queryFn: () => api.assetIndex(repoId!, { ...query, layout }),
    enabled: !!repoId && !searching,
  });
  const qkey = JSON.stringify([repoId, query, layout]);
  const [wanted, setWanted] = useState<number[]>([0]);
  useEffect(() => setWanted([0]), [qkey]);
  const pageData = useQueries({
    queries: searching ? [] : wanted.map((pg) => ({
      queryKey: ["assets", repoId, query, "page", pg],
      queryFn: () => api.assets(repoId!, { ...query, offset: pg * SPAGE, limit: SPAGE }),
      enabled: !!repoId,
    })),
    combine: (rs) => rs.map((r) => r.data),
  });
  const searchItems = useMemo(() => searchQ.data?.pages.flatMap((p) => p.items) ?? [], [searchQ.data]);
  const ids = useMemo(() => (searching ? searchItems.map((i) => i.file_id) : indexQ.data?.ids ?? []), [searching, searchItems, indexQ.data]);
  const items = useMemo<(AssetItem | undefined)[]>(() => {
    if (searching) return searchItems;
    const arr: (AssetItem | undefined)[] = new Array(ids.length);
    pageData.forEach((d, k) => d?.items.forEach((it, j) => { const i = wanted[k] * SPAGE + j; if (i < arr.length) arr[i] = it; }));
    return arr;
  }, [searching, searchItems, ids, pageData, wanted]);
  const runs = useMemo<Run[]>(() => {
    if (!searching) return indexQ.data?.runs ?? [];
    const out: Run[] = [];
    for (const it of searchItems) {
      const k = it.match ?? "text";
      if (!out.length || out[out.length - 1].k !== k) out.push({ k, n: 0 });
      out[out.length - 1].n++;
    }
    return out;
  }, [searching, searchItems, indexQ.data]);
  const total = ids.length;
  const listQ = searching ? searchQ : indexQ;
  const assets = { isLoading: listQ.isLoading, isError: listQ.isError, isFetching: listQ.isFetching, error: listQ.error, refetch: () => listQ.refetch() };
  const facets = useFacets(repoId, query.scope);
  const smart = useQuery({ queryKey: ["smart", repoId], queryFn: () => api.smart(repoId!), enabled: !!repoId });
  const folderQ = useQuery({
    queryKey: ["folders", repoId, filter.folder],
    queryFn: () => api.folders(repoId!, filter.folder),
    enabled: !!repoId && folderMode && !searching,
  });
  const subfolders = folderMode && !searching ? folderQ.data?.folders ?? [] : [];

  // Klasörler arasında gezinme (Finder gibi geri/ileri geçmişi)
  const navigate = useCallback((path: string) => {
    if (path === filter.folder) return;
    setHist((h) => ({ back: [...h.back, filter.folder], fwd: [] }));
    setSelFolder(null);
    setFilter({ ...filter, folder: path });
  }, [filter, setFilter]);
  const goBack = useCallback(() => {
    const prev = hist.back[hist.back.length - 1];
    if (prev === undefined) return;
    setHist((h) => ({ back: h.back.slice(0, -1), fwd: [filter.folder, ...h.fwd] }));
    setSelFolder(filter.folder || null);
    setFilter({ ...filter, folder: prev });
  }, [hist, filter, setFilter]);
  const goFwd = useCallback(() => {
    const next = hist.fwd[0];
    if (next === undefined) return;
    setHist((h) => ({ back: [...h.back, filter.folder], fwd: h.fwd.slice(1) }));
    setSelFolder(null);
    setFilter({ ...filter, folder: next });
  }, [hist, filter, setFilter]);
  const goUp = useCallback(() => {
    if (!filter.folder) return;
    const parent = filter.folder.includes("/") ? filter.folder.slice(0, filter.folder.lastIndexOf("/")) : "";
    const cur = filter.folder;
    navigate(parent);
    setSelFolder(cur);
  }, [filter.folder, navigate]);
  useEffect(() => { if (!folderMode) setHist({ back: [], fwd: [] }); }, [folderMode]);
  const [hideSmartTip, setHideSmartTip] = useState(() => localStorage.getItem("mm.hideSmartTip") === "1");

  useEffect(() => { setSelected(null); setSelIds(new Set()); }, [query]);
  useEffect(() => setSelection([...selIds]), [selIds, setSelection]);
  // Liste değişince (canlı izleme, silme) artık olmayan seçimleri bırak
  useEffect(() => {
    const all = new Set(ids);
    setSelIds((s) => ([...s].every((id) => all.has(id)) ? s : new Set([...s].filter((id) => all.has(id)))));
  }, [ids]);

  useEffect(() => {
    if (!scrollEl) return;
    const update = () => { if (scrollEl.clientWidth > 0) setWidth(scrollEl.clientWidth - PAD * 2); };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(scrollEl);
    return () => ro.disconnect();
  }, [scrollEl]);

  const w = width || 900;
  const cols = Math.max(1, Math.round((w + GAP) / (tile + GAP)));
  const size = Math.floor((w - (cols - 1) * GAP) / cols);

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    let start = 0;
    for (const run of runs) {
      const key = run.k;
      const end = start + run.n;
      const title = searching
        ? (key === "visual" ? (filter.similar ? `“${filter.simlabel || "görsel"}” fotoğrafına benzeyenler` : "Görsel olarak benzeyenler") : "Adı, yeri ya da etiketi eşleşenler")
        : grouping === "month" ? monthLabel(key) : key || "Ana klasör";
      if (!folderMode || searching) out.push({ type: "header", key: `h-${key}-${start}`, title, sub: searching ? "" : run.p ?? "", count: run.n,
        month: !searching && grouping === "month" && key !== "?" ? key : undefined });
      for (let r = start; r < end; r += cols) out.push({ type: "tiles", key: `t-${start}-${r}`, start: r, end: Math.min(r + cols, end) });
      start = end;
    }
    return out;
  }, [runs, cols, grouping, searching, folderMode, filter.similar, filter.simlabel]);

  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollEl,
    estimateSize: (i) => (rows[i].type === "header" ? 56 : size + GAP),
    overscan: 6,
    scrollMargin: margin,
  });
  // Zaman çizelgesi işaretleri: her ay başlığının ızgaradaki konumu
  const scrub = useMemo(() => {
    const marks: ScrubMark[] = [];
    let off = 0;
    for (const r of rows) {
      if (r.type === "header") {
        if (r.month) marks.push({ offset: off, year: r.month.slice(0, 4), label: r.title });
        off += 56;
      } else off += size + GAP;
    }
    return { marks, total: off };
  }, [rows, size]);
  // Izgaranın üstündeki alan (uyarılar, klasörler) değişince sanal listenin başlangıcını güncelle
  useLayoutEffect(() => {
    const el = aboveRef.current;
    if (!el) return;
    const update = () => setMargin(gridRef.current?.offsetTop ?? 0);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => virt.measure(), [size, virt]);

  // Görünen satırların (ve bir sonraki sayfanın) ayrıntılarını yükle
  const vItems = virt.getVirtualItems();
  const vFirst = vItems[0]?.index ?? -1;
  const vLast = vItems[vItems.length - 1]?.index ?? -1;
  useEffect(() => {
    if (searching || vFirst < 0) return;
    let lo = Infinity, hi = -1;
    for (let r = vFirst; r <= vLast; r++) {
      const row = rows[r];
      if (row?.type === "tiles") { lo = Math.min(lo, row.start); hi = Math.max(hi, row.end - 1); }
    }
    if (hi < 0) return;
    const need: number[] = [];
    const last = Math.floor((total - 1) / SPAGE);
    for (let pg = Math.max(0, Math.floor(lo / SPAGE) - 1); pg <= Math.min(last, Math.floor(hi / SPAGE) + 1); pg++) need.push(pg);
    setWanted((w) => (need.every((pg) => w.includes(pg)) ? w : [...new Set([...w, ...need])].sort((a, b) => a - b)));
  }, [searching, vFirst, vLast, rows, total]);
  const loadAll = useCallback(() => {
    if (searching || !total) return;
    setWanted(Array.from({ length: Math.ceil(total / SPAGE) }, (_, i) => i));
  }, [searching, total]);

  const selectOnly = useCallback((i: number | null) => {
    setSelected(i);
    setSelIds(i === null || ids[i] === undefined ? new Set() : new Set([ids[i]]));
  }, [ids]);
  const onSelect = useCallback((i: number, e: React.MouseEvent) => {
    const id = ids[i];
    if (id === undefined) return;
    if (e.metaKey) {
      // ⌘: seçime ekle / çıkar
      setSelIds((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
      setSelected(i);
    } else if (e.shiftKey && selected !== null) {
      // ⇧: aralık seç
      const [a, b] = selected < i ? [selected, i] : [i, selected];
      setSelIds(new Set(ids.slice(a, b + 1)));
    } else selectOnly(i);
  }, [ids, selected, selectOnly]);
  const onMenu = useCallback((i: number, e: React.MouseEvent) => {
    const id = ids[i];
    if (id === undefined) return;
    // Sağ tık seçimin dışındaysa yalnız o öğe seçilir (Finder/Fotoğraflar gibi)
    if (!selIds.has(id)) selectOnly(i);
    else setSelected(i);
    setMenu({ x: e.clientX, y: e.clientY });
  }, [ids, selIds, selectOnly]);
  // Görselle arama: kütüphanedeki bir fotoğraf ya da dışarıdan sürüklenen bir dosya
  const findSimilar = useCallback((it: AssetItem) => {
    setQ("");
    setFilter({ ...filter, q: "", similar: `a:${it.asset_id}`, simlabel: it.rel_path.split("/").pop() ?? "" });
  }, [filter, setFilter]);
  const findSimilarTo = useCallback(async (src: File | string) => {
    try {
      const r = await api.searchImage(repoId!, src);
      setQ("");
      setFilter({ ...filter, q: "", similar: `q:${r.key}`, simlabel: typeof src === "string" ? src.split("/").pop() ?? r.name : src.name || r.name });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  }, [repoId, filter, setFilter, toast]);
  // macOS paylaşım menüsü (AirDrop, Mesajlar, Mail…). Küçültülmüş paylaşımda kopyalar hazırlanır, konum silinir.
  const share = useCallback(async (chosen: AssetItem[], size: "original" | "large", x: number, y: number) => {
    try {
      const { paths } = await api.sharePrepare(repoId!, chosen.map((c) => c.file_id), size);
      if (!paths.length) throw new Error("Paylaşılacak dosya hazırlanamadı");
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("share_files", { paths, x, y });
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  }, [repoId, toast]);
  const [dropping, setDropping] = useState(false);
  const dropRef = useRef({ page, findSimilarTo });
  dropRef.current = { page, findSimilarTo };
  useEffect(() => {
    // Masaüstünde Finder'dan bırakılan dosyalar pencere düzeyinde gelir
    if (!isTauri) return;
    let un: (() => void) | undefined;
    let alive = true;
    import("@tauri-apps/api/webview").then(({ getCurrentWebview }) => getCurrentWebview().onDragDropEvent((ev) => {
      if (dropRef.current.page !== "library") return;
      const p = ev.payload;
      if (p.type === "enter" || p.type === "over") setDropping(true);
      else if (p.type === "leave") setDropping(false);
      else if (p.type === "drop") {
        setDropping(false);
        const img = p.paths.find((x) => IMG_RE.test(x));
        if (img) dropRef.current.findSimilarTo(img);
      }
    })).then((u) => { if (alive) un = u; else u(); });
    return () => { alive = false; un?.(); };
  }, []);
  // Görüntüleyici, açılan öğenin çevresindeki yüklenmiş kesintisiz bölümle açılır
  const onOpen = useCallback((i: number) => {
    if (!items[i]) return;
    let a = i, b = i;
    while (a > 0 && items[a - 1]) a--;
    while (b < items.length - 1 && items[b + 1]) b++;
    openViewer(items.slice(a, b + 1) as AssetItem[], i - a);
  }, [items, openViewer]);

  // Klavye: oklar seçimi gezdirir, boşluk/Enter açar, ⌘+/⌘- yakınlaştırır
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (viewer || page !== "library" || (e.target as HTMLElement)?.closest("input, textarea, select")) return;
      if (e.metaKey && e.key.toLowerCase() === "i") { setInfoOpen((x) => !x); e.preventDefault(); return; }
      if (folderMode && e.metaKey && e.key === "ArrowUp") { goUp(); e.preventDefault(); return; }
      if (folderMode && e.metaKey && e.key === "[") { goBack(); e.preventDefault(); return; }
      if (folderMode && e.metaKey && e.key === "]") { goFwd(); e.preventDefault(); return; }
      if (folderMode && selFolder !== null && (e.key === "Enter" || (e.metaKey && e.key === "ArrowDown"))) { navigate(selFolder); e.preventDefault(); return; }
      if (e.metaKey && (e.key === "+" || e.key === "=")) { setZoom((z) => Math.min(SIZES.length - 1, z + 1)); e.preventDefault(); return; }
      if (e.metaKey && e.key === "-") { setZoom((z) => Math.max(0, z - 1)); e.preventDefault(); return; }
      if (e.metaKey && e.key.toLowerCase() === "a" && ids.length) { setSelIds(new Set(ids)); loadAll(); setSelected((x) => x ?? 0); e.preventDefault(); return; }
      if ((e.key === "Backspace" || e.key === "Delete") && selIds.size) { trash([...selIds], () => selectOnly(null)); e.preventDefault(); return; }
      if (selected === null) return;
      if (e.key === "ArrowRight") selectOnly(Math.min(ids.length - 1, selected + 1));
      else if (e.key === "ArrowLeft") selectOnly(Math.max(0, selected - 1));
      else if (e.key === "ArrowDown") selectOnly(Math.min(ids.length - 1, selected + cols));
      else if (e.key === "ArrowUp") selectOnly(Math.max(0, selected - cols));
      else if (e.key === "Enter" || e.key === " ") onOpen(selected);
      else if (e.key === "Escape") selectOnly(null);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [selected, selIds, ids, loadAll, onOpen, cols, viewer, page, folderMode, goUp, goBack, goFwd, navigate, selFolder, setInfoOpen, selectOnly, trash]);

  useEffect(() => {
    if (selected === null) return;
    const idx = rows.findIndex((r) => r.type === "tiles" && selected >= r.start && selected < r.end);
    if (idx >= 0) virt.scrollToIndex(idx, { align: "auto" });
  }, [selected, rows, virt]);

  const createSmart = async () => {
    const name = (saveSmart ?? "").trim();
    if (!name || !repoId) return;
    const rules: Record<string, string> = {};
    for (const k of ["year", "month", "place", "region", "camera", "tag", "person", "date_from", "date_to"] as const) if (filter[k]) rules[k] = filter[k];
    if (filter.q.trim()) rules.search = filter.q.trim();
    if (view === "photo" || view === "video") rules.kind = view;
    if (view === "favorites") rules.favorite = "1";
    const r = await api.createAlbum(repoId, { name, kind: "smart", rules });
    qc.invalidateQueries({ queryKey: ["albums", repoId] });
    setSaveSmart(null);
    toast(`“${name}” akıllı albümü oluşturuldu`);
    setView("album");
    setFilter({ ...EMPTY_FILTER, album: String(r.id), label: name });
  };

  if (!repoId) return null;
  const sel = selected !== null ? items[selected] ?? null : null;
  const untracked = s?.counts.untracked ?? 0;
  const missing = s?.counts.missing ?? 0;
  const agentPlans = (s?.pending_plan_list ?? []).filter((p) => p.source !== "ui" && p.source !== "cli");
  const srcName = (src: string) => (src === "assistant" ? "Asistan" : src.startsWith("mcp:") ? src.slice(4) : src);
  const trackAll = async () => trackJob(await api.track(repoId));
  const rescan = async () => trackJob(await api.scan(repoId));

  // Etkin filtreler (jeton olarak gösterilir)
  const f = facets.data;
  const chips: SearchToken[] = [];
  const clear = (k: keyof LibraryFilter) => (x: LibraryFilter) => ({ ...x, [k]: "" });
  for (const pid of filter.person ? filter.person.split(",") : []) {
    chips.push({ key: `p${pid}`, kind: "person", label: f?.people.find((p) => String(p.id) === pid)?.name ?? "Kişi",
      remove: (x) => ({ ...x, person: x.person.split(",").filter((v) => v !== pid).join(",") }) });
  }
  if (filter.place) chips.push({ key: "place", kind: "place", label: filter.place, remove: clear("place") });
  if (filter.region) chips.push({ key: "region", kind: "region", label: filter.region, remove: clear("region") });
  if (filter.year) chips.push({ key: "year", kind: "year", label: filter.year, remove: clear("year") });
  if (filter.month) chips.push({ key: "month", kind: "month", label: monthName(filter.month), remove: clear("month") });
  if (filter.date_from && view !== "album") chips.push({ key: "date", kind: "date", label: filter.label || (filter.date_to && filter.date_to !== filter.date_from ? `${filter.date_from} – ${filter.date_to}` : filter.date_from),
    remove: (x) => ({ ...x, date_from: "", date_to: "", label: "" }) });
  for (const t of filter.tag ? filter.tag.split("|") : []) {
    chips.push({ key: `t${t}`, kind: "tag", label: t, remove: (x) => ({ ...x, tag: x.tag.split("|").filter((v) => v !== t).join("|") }) });
  }
  if (filter.camera) chips.push({ key: "camera", kind: "camera", label: filter.camera, remove: clear("camera") });
  if (filter.similar) chips.push({ key: "similar", kind: "similar", label: `Benzer: ${filter.simlabel || "görsel"}`, remove: (x) => ({ ...x, similar: "", simlabel: "" }) });
  if (filter.folder && !folderMode) chips.push({ key: "folder", kind: "album", label: filter.folder, remove: clear("folder") });
  const anyFilter = chips.length > 0 || searching;
  const kindSeg = view === "all" || view === "photo" || view === "video";
  const crumbs = filter.folder ? filter.folder.split("/") : [];
  const album = view === "album" ? albumsQ.data?.albums.find((a) => String(a.id) === filter.album) : undefined;
  const title = folderMode ? (crumbs[crumbs.length - 1] ?? s?.repo.name ?? "Klasörler") : view === "album" ? (album?.name ?? filter.label ?? "Albüm") : VIEW_TITLE[view];
  const subtitle = folderMode && !searching
    ? [subfolders.length ? `${fmtInt(subfolders.length)} klasör` : null, `${fmtInt(total)} öğe`].filter(Boolean).join(", ")
    : assets.isLoading ? (searching ? "Aranıyor…" : "") : searching ? `${fmtInt(total)} sonuç` : `${fmtInt(total)} ${view === "video" ? "video" : "öğe"}`;

  return (
    <div className="content" style={{ position: "relative" }}
      onDragOver={(e) => { if (!isTauri && e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDropping(true); } }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDropping(false); }}
      onDrop={(e) => {
        if (isTauri) return;
        e.preventDefault();
        setDropping(false);
        const f = [...e.dataTransfer.files].find((x) => x.type.startsWith("image/") || IMG_RE.test(x.name));
        if (f) findSimilarTo(f);
      }}>
      {dropping && (
        <div className="drop-hint" aria-hidden="true">
          <Search size={28} />
          <b>Benzer fotoğrafları bul</b>
          <span>Bıraktığın fotoğrafa görsel olarak benzeyenler kütüphanende aranır. Dosya kopyalanmaz.</span>
        </div>
      )}
      <div className="main">
        <Toolbar title={title} subtitle={subtitle} lead={folderMode ? (
          <div className="row" style={{ gap: 0 }}>
            <button className="ibtn" disabled={!hist.back.length} onClick={goBack} aria-label="Geri" title="Geri (⌘[)"><Left size={16} /></button>
            <button className="ibtn" disabled={!hist.fwd.length} onClick={goFwd} aria-label="İleri" title="İleri (⌘])"><Right size={16} /></button>
          </div>
        ) : undefined}>
          {kindSeg && (
            <div className="seg" role="tablist" aria-label="Tür">
              {([["all", "Tümü"], ["photo", "Fotoğraflar"], ["video", "Videolar"]] as const).map(([v, t]) => (
                <button key={v} role="tab" aria-selected={view === v} className={cls(view === v && "on")} onClick={() => setView(v)}>{t}</button>
              ))}
            </div>
          )}
          {!searching && !folderMode && (
            <div className="seg" aria-label="Gruplama">
              <button className={cls(grouping === "month" && "on")} onClick={() => setGrouping("month")} title="Aylara göre grupla">Ay</button>
              <button className={cls(grouping === "folder" && "on")} onClick={() => setGrouping("folder")} title="Klasörlere göre grupla">Klasör</button>
            </div>
          )}
          <input className="zoom" type="range" min={0} max={SIZES.length - 1} step={1} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Önizleme boyutu (⌘+ / ⌘−)" title="Önizleme boyutu (⌘+ / ⌘−)" />
          <button ref={filterBtn} className={cls("ibtn", (filterOpen || chips.length > 0) && "on")} onClick={() => setFilterOpen((o) => !o)} aria-label="Filtrele" title="Filtrele">
            <Filter size={17} />
          </button>
          <button className={cls("ibtn", infoOpen && "on")} onClick={() => setInfoOpen((x) => !x)} aria-label="Bilgi" aria-pressed={infoOpen} title="Bilgi (⌘I)">
            <Info size={17} />
          </button>
          <SearchField q={q} setQ={setQ} filter={filter} setFilter={setFilter} view={view} setView={setView} facets={f} albums={albumsQ.data?.albums}
            tokens={chips} placeholder={folderMode && filter.folder ? `“${crumbs[crumbs.length - 1]}” içinde ara` : smart.data?.content_model ? "Kişi, yer, tarih ya da “kumsalda köpek”" : "Ara"} />
        </Toolbar>
        {filterOpen && <FilterMenu anchor={filterBtn.current} onClose={() => setFilterOpen(false)} filter={filter} setFilter={setFilter} facets={f} />}

        {folderMode && (
          <nav className="pathbar" aria-label="Konum">
            <button className={cls(!crumbs.length && "cur")} onClick={() => navigate("")}><Folder size={13} />{s?.repo.name ?? "Kütüphane"}</button>
            {crumbs.map((c, i) => (
              <span key={i} className="row" style={{ gap: 2 }}>
                <Right size={11} style={{ color: "var(--muted)" }} />
                <button className={cls(i === crumbs.length - 1 && "cur")} onClick={() => navigate(crumbs.slice(0, i + 1).join("/"))}>{c}</button>
              </span>
            ))}
          </nav>
        )}
        {chips.length > 0 && (
          <div className="filterbar">
            <span className="muted" style={{ fontSize: 12 }}>{fmtInt(chips.length)} süzgeç{chips.filter((c) => c.kind === "person").length > 1 ? " · seçilen kişilerin birlikte olduğu fotoğraflar" : ""}</span>
            <button className="btn sm ghost" onClick={() => { setFilter(EMPTY_FILTER); setQ(""); }}>Tümünü temizle</button>
            <span className="grow" />
            <button className="btn sm" onClick={() => setSaveSmart("")} title="Bu süzgeci akıllı albüm olarak kaydet; yeni fotoğraflar kendiliğinden eklenir">Akıllı albüm olarak kaydet</button>
          </div>
        )}
        {saveSmart !== null && (
          <div className="filterbar">
            <span className="muted" style={{ fontSize: 12 }}>Akıllı albüm adı:</span>
            <label className="field" style={{ width: 220 }}>
              <span className="sr">Albüm adı</span>
              <input autoFocus value={saveSmart} onChange={(e) => setSaveSmart(e.target.value)}
                onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") createSmart(); if (e.key === "Escape") setSaveSmart(null); }} />
            </label>
            <button className="btn sm pri" disabled={!saveSmart.trim()} onClick={createSmart}>Kaydet</button>
            <button className="btn sm ghost" onClick={() => setSaveSmart(null)}>Vazgeç</button>
          </div>
        )}

        <div style={{ flex: 1, minHeight: 0, display: "flex", position: "relative" }}>
        <div ref={setScrollEl} className="scroll" style={{ padding: `0 ${PAD}px 24px`, position: "relative" }}
          onClick={(e) => { if (e.target === e.currentTarget) { setSelFolder(null); selectOnly(null); } }}>
          <div ref={aboveRef}>
          <div className="col" style={{ gap: 8, paddingTop: 12 }}>
            {agentPlans.slice(0, 3).map((p) => (
              <div key={p.id} className="banner warn">
                <span className="grow"><b>{srcName(p.source)}</b> bir değişiklik öneriyor: {p.title || p.kind} ({fmtInt(p.summary.files)} dosya)</span>
                <button className="btn sm pri" onClick={async () => showPlan(await api.plan(repoId, p.id))}>İncele</button>
              </div>
            ))}
            {untracked > 0 && view !== "untracked" && (
              <div className="banner">
                <span className="grow"><b>{fmtInt(untracked)} yeni dosya</b> bulundu. Kütüphaneye eklediğinde tarih, yer ve önizlemeleri hazırlanır.</span>
                <button className="btn sm" onClick={() => setView("untracked")}>Göster</button>
                <button className="btn sm pri" onClick={trackAll} disabled={s?.busy}>Kütüphaneye ekle</button>
              </div>
            )}
            {view === "untracked" && untracked > 0 && (
              <div className="banner">
                <span className="grow">Bu dosyalar klasörde var ama henüz kütüphanede değil.</span>
                <button className="btn sm pri" onClick={trackAll} disabled={s?.busy}>Tümünü kütüphaneye ekle</button>
              </div>
            )}
            {missing > 0 && (
              <div className="banner warn">
                <span className="grow"><b>{fmtInt(missing)} dosya</b> artık klasörde değil (silinmiş ya da taşınmış).</span>
                <button className="btn sm" onClick={async () => { const r = await api.forgetMissing(repoId); toast(`${fmtInt(r.forgotten)} kayıt kaldırıldı`); rescan(); }}>Kütüphaneden kaldır</button>
              </div>
            )}
            {smart.data && !hideSmartTip && !smart.data.content_model && !smart.data.face_model && total > 0 && (
              <div className="banner">
                <span className="grow">Kişileri tanımak ve “kumsalda köpek” gibi aramalar için akıllı analizi aç. Her şey bu Mac'te çalışır.</span>
                <button className="btn sm pri" onClick={() => setPage("settings")}>Aç…</button>
                <button className="btn sm ghost" onClick={() => { setHideSmartTip(true); try { localStorage.setItem("mm.hideSmartTip", "1"); } catch { /* yok */ } }}>Şimdi değil</button>
              </div>
            )}
          </div>

          {subfolders.length > 0 && (
            <div className="fgrid" style={{ ["--fw" as string]: `${Math.max(104, Math.round(tile * 0.78))}px` }}>
              {subfolders.map((f) => (
                <FolderTile key={f.path} repoId={repoId} f={f} selected={selFolder === f.path}
                  onSelect={() => { setSelFolder(f.path); selectOnly(null); }} onOpen={() => navigate(f.path)}
                  onMenu={(el) => setFolderMenu({ el, f })} />
              ))}
            </div>
          )}
          {folderMode && !searching && subfolders.length > 0 && total > 0 && <div className="group-h"><h2>Bu klasördeki dosyalar</h2></div>}
          {assets.isLoading && <div className="loading-msg"><span className="spin" />Yükleniyor…</div>}
          {assets.isError && !assets.isFetching && total === 0 && (
            <div className="empty">
              <h2>Liste yüklenemedi</h2>
              <span>{(assets.error as Error)?.message}</span>
              <button className="btn" onClick={() => assets.refetch()}>Tekrar dene</button>
            </div>
          )}
          {!assets.isLoading && !assets.isError && total === 0 && !(folderMode && subfolders.length > 0) && (
            <div className="empty">
              {searching ? <Search size={40} /> : <Image size={40} />}
              <h2>{filter.similar ? "Benzer fotoğraf bulunamadı" : searching ? `“${filter.q}” için sonuç yok` : folderMode ? "Bu klasör boş" : view === "album" ? "Bu albüm boş" : anyFilter ? "Bu filtrelere uyan öğe yok" : view === "untracked" ? "Yeni dosya yok" : view === "favorites" ? "Henüz favori yok" : "Henüz fotoğraf yok"}</h2>
              {view === "album" && <span>{album?.kind === "smart" ? "Albümün süzgecine uyan fotoğraf yok." : "Kütüphanede fotoğraflara sağ tıklayıp “Albüme ekle”yi seç."}</span>}
              {view === "favorites" && !anyFilter && <span>Bir fotoğrafı favorilere eklemek için kalbe dokun ya da F tuşuna bas.</span>}
              {!anyFilter && view === "all" && untracked === 0 && <span>Kütüphane klasörüne fotoğraf ya da video ekle; MediaMind onları kendiliğinden bulur.</span>}
              {!anyFilter && view === "all" && untracked === 0 && <button className="btn" onClick={rescan}>Şimdi tara</button>}
              {anyFilter && <button className="btn" onClick={() => { setFilter(EMPTY_FILTER); setQ(""); }}>Filtreleri temizle</button>}
            </div>
          )}
          </div>

          <div ref={gridRef} style={{ height: virt.getTotalSize(), position: "relative" }}>
            {virt.getVirtualItems().map((v) => {
              const row = rows[v.index];
              return (
                <div key={row.key} style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${v.start - margin}px)` }}>
                  {row.type === "header" ? (
                    <div className="group-h">
                      <h2>{row.title}</h2>
                      {row.sub && <span className="muted ellipsis">{row.sub}</span>}
                    </div>
                  ) : (
                    <div style={{ display: "flex", gap: GAP, height: size + GAP }}>
                      {Array.from({ length: row.end - row.start }, (_, k) => {
                        const index = row.start + k;
                        const item = items[index];
                        return item
                          ? <Tile key={item.file_id} repoId={repoId} item={item} index={index} size={size} selected={selIds.has(item.file_id)} onSelect={onSelect} onOpen={onOpen} onMenu={onMenu} />
                          : <span key={`ph-${index}`} className="tile ph-tile" style={{ width: size, height: size }} aria-hidden="true" />;
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        {scrub.marks.length > 2 && !searching && !folderMode && <Scrubber marks={scrub.marks} total={scrub.total} scrollEl={scrollEl} margin={margin} />}
        </div>
      </div>
      {menu && (() => {
        const chosen = items.filter((x): x is AssetItem => !!x && selIds.has(x.file_id));
        if (!chosen.length) return null;
        const one = chosen.length === 1 ? chosen[0] : null;
        const allFav = chosen.every((x) => x.favorite);
        const untrackedPaths = chosen.filter((x) => !x.tracked).map((x) => x.rel_path);
        const n = chosen.length;
        const run = (fn: () => unknown) => () => { closeMenu(); fn(); };
        const setFav = async (v: boolean) => {
          try {
            await Promise.all([...new Set(chosen.map((x) => x.asset_id))].map((aid) => api.updateAsset(repoId, aid, { favorite: v })));
            qc.invalidateQueries({ queryKey: ["assets", repoId] });
            qc.invalidateQueries({ queryKey: ["asset", repoId] });
            toast(v ? `${fmtInt(n)} öğe favorilere eklendi` : `${fmtInt(n)} öğe favorilerden çıkarıldı`);
          } catch (e) {
            toast(String((e as Error).message), { error: true });
          }
        };
        return (
          <>
            <span key={`${menu.x}-${menu.y}`} ref={setMenuAnchor} style={{ position: "fixed", left: menu.x, top: menu.y - 4, width: 0, height: 0 }} />
            {menuAnchor && <Popover anchor={menuAnchor} onClose={closeMenu} width={230} align="start">
              {menuMode === "album" ? <AlbumAddMenu repoId={repoId} assetIds={[...new Set(chosen.map((x) => x.asset_id))]} onDone={closeMenu} /> : <>
              {n > 1 && <div className="lbl" style={{ padding: "4px 10px 2px" }}>{fmtInt(n)} öğe seçili</div>}
              {one && <button className="mitem" onClick={run(() => onOpen(items.indexOf(one)))}>Aç<span className="kbd-hint">Boşluk</span></button>}
              {one && smart.data?.content_model && <button className="mitem" onClick={run(() => findSimilar(one))}>Benzerlerini bul</button>}
              <button className="mitem" onClick={run(() => setInfoOpen(true))}>Bilgileri göster<span className="kbd-hint">⌘I</span></button>
              <div className="msep" />
              <button className="mitem" onClick={run(() => setFav(!allFav))}>{allFav ? "Favorilerden çıkar" : "Favorilere ekle"}</button>
              {untrackedPaths.length > 0 && (
                <button className="mitem" onClick={run(async () => trackJob(await api.track(repoId, untrackedPaths)))}>
                  Kütüphaneye ekle{untrackedPaths.length < n ? ` (${fmtInt(untrackedPaths.length)})` : ""}
                </button>
              )}
              {one && <button className="mitem" onClick={run(() => s?.repo.last_path && revealInFinder(`${s.repo.last_path}/${one.rel_path}`))}>Finder'da göster</button>}
              <div className="msep" />
              <button className="mitem" onMouseDown={(e) => e.preventDefault()} onClick={() => setMenuMode("album")}>Albüme ekle<span className="kbd-hint">›</span></button>
              {view === "album" && album?.kind === "manual" && (
                <button className="mitem" onClick={run(async () => {
                  await api.albumRemove(repoId, album.id, [...new Set(chosen.map((x) => x.asset_id))]);
                  qc.invalidateQueries({ queryKey: ["assets", repoId] }); qc.invalidateQueries({ queryKey: ["albums", repoId] });
                  toast(`${fmtInt(n)} öğe albümden çıkarıldı`);
                })}>Albümden çıkar</button>
              )}
              <button className="mitem" onClick={run(() => setDialog({ kind: "date", ids: [...new Set(chosen.map((x) => x.asset_id))], sample: chosen[0].taken_at }))}>Tarihi düzelt…</button>
              <button className="mitem" onClick={run(() => setDialog({ kind: "location", ids: [...new Set(chosen.map((x) => x.asset_id))] }))}>Konum ata…</button>
              <button className="mitem" onClick={run(() => setDialog({ kind: "export", ids: chosen.map((x) => x.file_id) }))}>Dışa aktar…</button>
              {isTauri && <>
                <button className="mitem" onClick={run(() => share(chosen, "original", menu.x, menu.y))}>Paylaş…</button>
                {chosen.some((x) => x.kind === "photo") && <button className="mitem" onClick={run(() => share(chosen, "large", menu.x, menu.y))} title="2048 piksele küçültülür, konum bilgisi silinir">Küçültüp paylaş…</button>}
              </>}
              {chosen.some((x) => x.kind === "video" && x.tracked) && (
                <button className="mitem" onClick={run(() => setDialog({ kind: "compress", ids: chosen.filter((x) => x.kind === "video" && x.tracked).map((x) => x.file_id), bytes: chosen.filter((x) => x.kind === "video").reduce((a, x) => a + x.size, 0) }))}>
                  {chosen.filter((x) => x.kind === "video").length > 1 ? "Videoları küçült…" : "Videoyu küçült…"}
                </button>
              )}
              <div className="msep" />
              <button className="mitem danger" onClick={run(() => trash(chosen.map((x) => x.file_id), () => selectOnly(null)))}>
                {n > 1 ? `${fmtInt(n)} öğeyi sil…` : "Sil…"}<span className="kbd-hint">⌫</span>
              </button>
              </>}
            </Popover>}
          </>
        );
      })()}
      {dialog?.kind === "date" && <DateDialog repoId={repoId} assetIds={dialog.ids} sample={dialog.sample ?? null} onClose={closeDialog} />}
      {dialog?.kind === "location" && <LocationDialog repoId={repoId} assetIds={dialog.ids} onClose={closeDialog} />}
      {dialog?.kind === "export" && <ExportDialog repoId={repoId} fileIds={dialog.ids} onClose={closeDialog} />}
      {dialog?.kind === "compress" && <CompressDialog repoId={repoId} fileIds={dialog.ids} bytes={dialog.bytes} onClose={closeDialog} onDone={() => selectOnly(null)} />}
      {folderMenu && (
        <Popover anchor={folderMenu.el} onClose={closeFolderMenu} width={200} align="start">
          <button className="mitem" onClick={() => { closeFolderMenu(); navigate(folderMenu.f.path); }}>Aç</button>
          <button className="mitem" onClick={() => { closeFolderMenu(); if (s) revealInFinder(`${s.repo.last_path}/${folderMenu.f.path}`); }}>Finder'da göster</button>
        </Popover>
      )}
      {infoOpen && (sel
        ? <DetailPanel repoId={repoId} item={sel} onClose={() => setInfoOpen(false)} onOpen={() => onOpen(selected!)} />
        : (
          <aside className="detail" aria-label="Bilgi">
            <div className="row" style={{ minHeight: 28 }}>
              <span className="grow" style={{ fontWeight: 600 }}>Bilgi</span>
              <button className="ibtn" onClick={() => setInfoOpen(false)} aria-label="Kapat"><Close size={15} /></button>
            </div>
            <div className="empty" style={{ padding: "60px 8px" }}>
              <Info size={32} />
              <span>{selFolder ? `“${selFolder.split("/").pop()}” klasörü seçili. Açmak için çift tıkla.` : "Ayrıntılarını görmek için bir fotoğraf ya da video seç."}</span>
            </div>
          </aside>
        ))}
    </div>
  );
}
