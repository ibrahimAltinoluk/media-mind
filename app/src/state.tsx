import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { AssetItem, Job, Plan } from "./types";

export type Page =
  | "library" | "places" | "people" | "dups" | "organize" | "history" | "trash" | "settings" | "assistant" | "memories" | "cleanup" | "import";

export type LibraryView = "all" | "photo" | "video" | "untracked" | "favorites" | "folders" | "album";

export interface LibraryFilter {
  q: string;
  year: string;
  place: string;
  camera: string;
  folder: string;
  tag: string;
  person: string; // kişi kimliği (string olarak)
  semantic: boolean;
  date_from: string; // YYYY-MM-DD (anılar, tarih aralığı)
  date_to: string;
  album: string; // albüm kimliği (view === "album")
  label: string; // tarih aralığı/albüm için gösterilecek ad
  month: string; // "07": her yılın temmuzu
  region: string; // il/eyalet (yerin bütünü)
  similar: string; // görselle arama: "a:<asset_id>" ya da "q:<anahtar>"
  simlabel: string; // jetonda gösterilecek ad
}

export const EMPTY_FILTER: LibraryFilter = { q: "", year: "", place: "", camera: "", folder: "", tag: "", person: "", semantic: false, date_from: "", date_to: "", album: "", label: "", month: "", region: "", similar: "", simlabel: "" };

export type Theme = "system" | "light" | "dark";

/** İş türü → bittiğinde yenilenecek sorgular (listede olmayan türler her şeyi yeniler: tarama, plan, içe aktarma…) */
const PEOPLE_KEYS = ["people", "personFaces", "mergeSug", "asset", "facets", "summary"];
const JOB_AFFECTS: Record<string, string[]> = {
  people: PEOPLE_KEYS,
  people_pets: PEOPLE_KEYS,
  smart: [...PEOPLE_KEYS, "smart", "video", "transcript", "similar", "cleanup", "memories", "map"],
  models: ["models", "mlmodels", "modelTiers", "smart", "settings", "summary"],
  export: ["summary"],
  proxy: ["summary", "asset", "video"],
};

interface Toast { id: number; text: string; error?: boolean; action?: { label: string; run: () => void } }

interface Ctx {
  repoId: string | null;
  setRepoId: (id: string | null) => void;
  page: Page;
  setPage: (p: Page) => void;
  view: LibraryView;
  setView: (v: LibraryView) => void;
  filter: LibraryFilter;
  setFilter: (f: LibraryFilter) => void;
  /** trash: Son silinenler'den açıldıysa dosya → çöp öğesi eşlemesi (görüntüleyici "Geri getir / Kalıcı sil" sunar) */
  viewer: { items: AssetItem[]; index: number; trash?: Record<number, number> } | null;
  openViewer: (items: AssetItem[], index: number, opts?: { trash?: Record<number, number> }) => void;
  closeViewer: () => void;
  setViewerIndex: (i: number) => void;
  plan: Plan | null;
  showPlan: (p: Plan | null, onDone?: () => void) => void;
  planDone: (() => void) | null;
  assistantOpen: boolean;
  toggleAssistant: () => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  toasts: Toast[];
  toast: (text: string, opts?: { error?: boolean; action?: Toast["action"] }) => void;
  dismissToast: (id: number) => void;
  jobs: Job[];
  trackJob: (j: Job) => void;
  selection: number[];
  setSelection: (ids: number[]) => void;
  deviceId: string | null;
  setDeviceId: (id: string | null) => void;
}

const AppCtx = createContext<Ctx | null>(null);

function load<T>(key: string, def: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : def;
  } catch {
    return def;
  }
}
function save(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* depolama kapalı */
  }
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [repoId, setRepoIdRaw] = useState<string | null>(() => load("mm.repo", null));
  const [page, setPage] = useState<Page>("library");
  const [view, setView] = useState<LibraryView>("all");
  const [filter, setFilter] = useState<LibraryFilter>(EMPTY_FILTER);
  const [viewer, setViewer] = useState<Ctx["viewer"]>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planDone, setPlanDone] = useState<(() => void) | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [theme, setThemeRaw] = useState<Theme>(() => load("mm.theme", "system"));
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [watched, setWatched] = useState<number[]>([]);
  const [selection, setSelection] = useState<number[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const toastId = useRef(1);

  const setRepoId = useCallback((id: string | null) => {
    setRepoIdRaw(id);
    save("mm.repo", id);
    setFilter(EMPTY_FILTER);
    setView("all");
    setPage("library");
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeRaw(t);
    save("mm.theme", t);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toast = useCallback((text: string, opts?: { error?: boolean; action?: Toast["action"] }) => {
    const id = toastId.current++;
    setToasts((t) => [...t, { id, text, ...opts }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), opts?.action ? 9000 : 5000);
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  // İş takibi: çalışan iş varken sık, yokken seyrek sorgula; biten iş verileri tazeler.
  const jobsQ = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs(),
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => j.status === "running" || j.status === "queued") || watched.length ? 700 : 5000),
  });
  const jobs = useMemo(() => jobsQ.data ?? [], [jobsQ.data]);
  const prevStatus = useRef<Record<number, string>>({});
  const startedAt = useRef(Date.now() / 1000);
  useEffect(() => {
    // Biten işin türüne göre yalnız etkilenen veriler yenilenir (her iş bitiminde bütün sayfaların yenilenmesi
    // ızgaranın titremesine ve gereksiz beklemeye yol açıyordu)
    const keys = new Set<string>();
    let all = false;
    let changed = false;
    for (const j of jobs) {
      const before = prevStatus.current[j.id];
      const ended = j.status === "done" || j.status === "failed" || j.status === "cancelled";
      // İki yoklama arasında başlayıp biten işler de (ör. canlı izlemenin kısa taramaları) sayılır
      const fresh = !before && ended && (j.finished_at ?? 0) > startedAt.current;
      if (ended && ((before && before !== j.status) || fresh)) {
        changed = true;
        const affected = JOB_AFFECTS[j.type];
        if (!affected) all = true;
        else for (const k of affected) keys.add(k);
        if (j.status === "failed") toast(`${j.title} başarısız: ${j.error}`, { error: true });
        if (j.status === "done" && j.type === "scan" && j.title.includes("algılandı")) {
          const c = (j.result?.counts ?? {}) as Record<string, number>;
          const parts = [c.new && `${c.new} yeni dosya`, c.copies && `${c.copies} kopya`, c.moved && `${c.moved} taşınmış`, c.missing && `${c.missing} silinmiş`].filter(Boolean);
          if (parts.length) toast(`Klasörde değişiklik: ${parts.join(", ")}`);
        }
      }
      prevStatus.current[j.id] = j.status;
    }
    if (changed) {
      qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs" && (all || keys.has(String(q.queryKey[0]))) });
      setWatched((w) => w.filter((id) => jobs.find((j) => j.id === id)?.status === "running"));
    }
  }, [jobs, qc, toast]);

  const trackJob = useCallback(
    (j: Job) => {
      prevStatus.current[j.id] = j.status;
      setWatched((w) => [...w, j.id]);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    [qc],
  );

  const value: Ctx = {
    repoId, setRepoId, page, setPage, view, setView, filter, setFilter,
    viewer,
    openViewer: (items, index, opts) => setViewer({ items, index, trash: opts?.trash }),
    closeViewer: () => setViewer(null),
    setViewerIndex: (i) => setViewer((v) => (v ? { ...v, index: i } : v)),
    plan,
    showPlan: (p, onDone) => {
      setPlan(p);
      setPlanDone(() => onDone ?? null);
    },
    planDone,
    assistantOpen,
    toggleAssistant: () => setAssistantOpen((o) => !o),
    theme, setTheme, toasts, toast, dismissToast, jobs, trackJob, selection, setSelection, deviceId, setDeviceId,
  };
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("AppProvider eksik");
  return c;
}

export function useSummary(repoId: string | null) {
  // Ajanlar (MCP) plan önerebildiği için özet düzenli tazelenir
  return useQuery({ queryKey: ["summary", repoId], queryFn: () => api.summary(repoId!), enabled: !!repoId, retry: false, refetchInterval: 8000 });
}
