import { invoke } from "@tauri-apps/api/core";
import type { Album, CleanupCategory, Memory, FolderList, VideoInfo, AppSettings, AssetDetail, AssetItem, ChatResult, DupsResponse, FaceCluster, Facets, GeoSuggestions, IgnoredGroup, Job, ModelStatus, OrganizePreview, Person, Plan, Probe, RepoEntry, SmartState, Summary, TrashInfo } from "./types";

/** [enlem, boylam, blake3, dosya no, tarih, yer, tür, öğe no, yol] */
export type MapPoint = [number, number, string, number, string | null, string | null, "photo" | "video", number, string];

export type DeviceInfo = { id: string; name: string; product: string; ready: boolean; locked: boolean; count: number | null; kind: "device" | "card" };
export type TierKind = "speech";
export type Tier = "standard" | "high";
export interface TierOption { tier: Tier; model: string; size: number; downloaded: number; installed: boolean; allowed: boolean; reason: string | null; license: string }
export interface TierStatus {
  hardware: { chip: string; apple_silicon: boolean; ram_gb: number | null; cores: number; free_disk: number };
  kinds: Record<TierKind, { label: string; current: Tier; options: TierOption[] }>;
  progress?: Record<TierKind, { done: number; total: number }>;
}
export interface TierTrial {
  kind: TierKind; tier: Tier; sample?: number; per_item?: number; per_item_current?: number | null; items?: number; photos?: number; videos?: number;
  video_seconds?: number; transcripts_on?: boolean; rtf?: number | null; rtf_current?: number | null; estimate_s: number | null;
}
export type DeviceItem = { key: string; name: string; size: number; created: string | null; kind: "photo" | "video"; imported?: boolean; folder?: string; duration_ms?: number | null };
export type MiniPerson = { id: number; name: string; cover: number | null; photos: number };

export const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let backend: { url: string; token: string } = {
  url: import.meta.env.VITE_MM_URL ?? "http://127.0.0.1:7341",
  token: import.meta.env.VITE_MM_TOKEN ?? "",
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function initBackend(): Promise<void> {
  if (isTauri) backend = await invoke<{ url: string; token: string }>("backend_info");
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${backend.url}/health`);
      if (r.ok) return;
    } catch {
      /* henüz hazır değil */
    }
    await new Promise((res) => setTimeout(res, 200));
  }
  throw new Error(`MediaMind çekirdeğine bağlanılamadı (${backend.url})`);
}

type Query = Record<string, string | number | boolean | undefined | null>;

function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

// ------------------------------------------------------------------ süren işlemler (kullanıcıya ne beklediğini söyler)

/** Yazma isteklerinin kullanıcıya gösterilen adı; null → gösterilmez (kendi arayüzü olanlar) */
const BUSY_LABELS: [RegExp, string | null][] = [
  [/\/assistant$/, null],
  [/\/jobs\/\d+\/cancel$/, null],
  [/\/people\/clusters\/\d+\/name$/, "Kişi adlandırılıyor"],
  [/\/people\/clusters\/\d+\/(reject|dissolve|ignore)/, "Grup güncelleniyor"],
  [/\/people\/ignored\/\d+\/restore$/, "Grup geri getiriliyor"],
  [/\/people\/\d+\/merge$/, "Kişiler birleştiriliyor"],
  [/\/people\/\d+\/cover$/, "Kapak fotoğrafı değiştiriliyor"],
  [/^DELETE .*\/people\/\d+$/, "Kişi kaldırılıyor"],
  [/\/people\/\d+$/, "Kişi güncelleniyor"],
  [/\/faces\/\d+/, "Yüz etiketi güncelleniyor"],
  [/\/assets\/\d+\/people$/, "Kişi ekleniyor"],
  [/\/plans\/\d+\/apply$/, "Değişiklikler uygulanıyor"],
  [/\/plans\/\d+\/undo$/, "Geri alınıyor"],
  [/\/plans$/, "Plan hazırlanıyor"],
  [/\/trash/, "Son silinenler güncelleniyor"],
  [/\/albums/, "Albüm güncelleniyor"],
  [/^PATCH .*\/assets\/\d+$/, "Kaydediliyor"],
  [/\/(favorite|rating|tags)/, "Kaydediliyor"],
  [/\/edits|\/dates|\/location/, "Değişiklik kaydediliyor"],
  [/\/geo\/suggest$/, null],
  [/\/geo\/apply$/, "Konumlar kaydediliyor"],
  [/\/import$/, "İçe aktarma başlatılıyor"],
  [/\/export$/, "Dışa aktarma başlatılıyor"],
  [/\/share\/prepare$/, "Paylaşım hazırlanıyor"],
  [/\/search\/image$/, "Görsel inceleniyor"],
  [/\/(scan|track|analyze|smart)/, "İş başlatılıyor"],
];
export interface BusyOp { id: number; label: string; started: number }
let busyOps: BusyOp[] = [];
let busySeq = 0;
const busySubs = new Set<(ops: BusyOp[]) => void>();
const emitBusy = () => busySubs.forEach((f) => f(busyOps));
export function subscribeBusy(fn: (ops: BusyOp[]) => void): () => void {
  busySubs.add(fn);
  fn(busyOps);
  return () => busySubs.delete(fn);
}
function busyLabel(method: string, path: string): string | null {
  const key = `${method} ${path}`;
  for (const [re, label] of BUSY_LABELS) if (re.test(key) || re.test(path)) return label;
  return "İşleniyor";
}

// Aynı yazma isteği sürerken tekrar gönderilmez (çift tıklama aynı işlemi iki kez yapmasın): aynı söz döner
const pendingWrites = new Map<string, Promise<unknown>>();

export async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown; timeout?: number } = {}): Promise<T> {
  if (method === "GET") return send<T>(method, path, opts);
  const key = `${method} ${path}${qs(opts.query)} ${opts.body !== undefined ? JSON.stringify(opts.body) : ""}`;
  const same = pendingWrites.get(key);
  if (same) return same as Promise<T>;
  const label = busyLabel(method, path);
  const op: BusyOp | null = label ? { id: ++busySeq, label, started: Date.now() } : null;
  if (op) { busyOps = [...busyOps, op]; emitBusy(); }
  const p = send<T>(method, path, opts).finally(() => {
    pendingWrites.delete(key);
    if (op) { busyOps = busyOps.filter((o) => o.id !== op.id); emitBusy(); }
  });
  pendingWrites.set(key, p);
  return p;
}

/** Ham dosya gövdesiyle POST (sürüklenen görsel) */
async function sendRaw<T>(path: string, file: File): Promise<T> {
  const r = await fetch(`${backend.url}${path}`, { method: "POST", headers: { "x-mm-token": backend.token, "content-type": file.type || "application/octet-stream" }, body: file });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* gövde yok */ }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

async function send<T>(method: string, path: string, opts: { query?: Query; body?: unknown; timeout?: number } = {}): Promise<T> {
  // Okuma istekleri takılı kalmasın: süre aşılınca iptal edilir, TanStack Query yeniden dener.
  // (Yazma istekleri ve asistan uzun sürebileceği için varsayılan olarak süre sınırı yoktur.)
  const timeout = opts.timeout ?? (method === "GET" ? 20_000 : 0);
  const ctl = timeout ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), timeout) : null;
  let r: Response;
  try {
    r = await fetch(`${backend.url}${path}${qs(opts.query)}`, {
      method,
      headers: { "x-mm-token": backend.token, ...(opts.body !== undefined ? { "content-type": "application/json" } : {}) },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: ctl?.signal,
    });
  } catch (e) {
    if (ctl?.signal.aborted) throw new ApiError(408, "Çekirdek zamanında yanıt vermedi");
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* gövde yok */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export const thumbUrl = (repo: string, blake: string, size: "s" | "m" | "l") =>
  `${backend.url}/repos/${repo}/thumb/${blake}/${size}?t=${backend.token}`;
export const deviceThumbUrl = (device: string, key: string) =>
  `${backend.url}/devices/thumb?device=${encodeURIComponent(device)}&key=${encodeURIComponent(key)}&t=${backend.token}`;
export const displayUrl = (repo: string, blake: string) => `${backend.url}/repos/${repo}/display/${blake}?t=${backend.token}`;
export const originalUrl = (repo: string, blake: string) => `${backend.url}/repos/${repo}/original/${blake}?t=${backend.token}`;
export const backendUrl = () => backend.url;
export const playUrl = (repo: string, blake: string) => `${backend.url}/repos/${repo}/play/${blake}?t=${backend.token}`;
export const stripUrl = (repo: string, blake: string, n: number) => `${backend.url}/repos/${repo}/strip/${blake}?n=${n}&t=${backend.token}`;
export const frameUrl = (repo: string, blake: string, ms: number) => `${backend.url}/repos/${repo}/frame/${blake}/${ms}?t=${backend.token}`;
export const faceUrl = (repo: string, faceId: number, size?: 400) => `${backend.url}/repos/${repo}/faces/${faceId}/crop?t=${backend.token}${size ? `&size=${size}` : ""}`;

export const api = {
  repos: () => request<RepoEntry[]>("GET", "/repos"),
  probe: (path: string) => request<Probe>("GET", "/probe", { query: { path } }),
  addRepo: (path: string, name?: string) => request<RepoEntry>("POST", "/repos", { body: { path, name } }),
  removeRepo: (id: string) => request<{ ok: boolean }>("DELETE", `/repos/${id}`),
  renameRepo: (id: string, name: string) => request<RepoEntry>("PATCH", `/repos/${id}`, { body: { name } }),
  summary: (id: string) => request<Summary>("GET", `/repos/${id}/summary`),
  updateConfig: (id: string, patch: Record<string, unknown>) => request<Record<string, unknown>>("PATCH", `/repos/${id}/config`, { body: patch }),
  scan: (id: string) => request<Job>("POST", `/repos/${id}/scan`),
  track: (id: string, paths?: string[]) => request<Job>("POST", `/repos/${id}/track`, { body: { paths: paths ?? null } }),
  analyze: (id: string, force = false) => request<Job>("POST", `/repos/${id}/analyze`, { body: { force } }),
  forgetMissing: (id: string) => request<{ forgotten: number }>("POST", `/repos/${id}/forget-missing`),
  cancelJob: (id: number) => request<Job>("POST", `/jobs/${id}/cancel`),
  jobs: (repo?: string) => request<Job[]>("GET", "/jobs", { query: { repo } }),
  job: (jid: number) => request<Job>("GET", `/jobs/${jid}`),
  searchImage: (id: string, src: File | string) =>
    typeof src === "string"
      ? request<{ key: string; name: string }>("POST", `/repos/${id}/search/image`, { body: { path: src }, timeout: 60_000 })
      : sendRaw<{ key: string; name: string }>(`/repos/${id}/search/image`, src),
  sharePrepare: (id: string, file_ids: number[], size: "original" | "large") =>
    request<{ paths: string[] }>("POST", `/repos/${id}/share/prepare`, { body: { file_ids, size }, timeout: 0 }),
  assetIndex: (id: string, query: Query) =>
    request<{ total: number; ids: number[]; runs: { k: string; n: number; p?: string }[] }>("GET", `/repos/${id}/assets`, { query, timeout: 60_000 }),
  assets: (id: string, query: Query) => request<{ total: number; offset: number; items: AssetItem[] }>("GET", `/repos/${id}/assets`, { query, timeout: query.search ? 90_000 : 30_000 }),
  cleanup: (id: string) => request<{ categories: CleanupCategory[] }>("GET", `/repos/${id}/cleanup`, { timeout: 60_000 }),
  cleanupKeep: (id: string, category: string, asset_ids: number[]) => request("POST", `/repos/${id}/cleanup/keep`, { body: { category, asset_ids } }),
  memories: (id: string) => request<{ memories: Memory[] }>("GET", `/repos/${id}/memories`, { timeout: 60_000 }),
  albums: (id: string) => request<{ albums: Album[] }>("GET", `/repos/${id}/albums`),
  createAlbum: (id: string, body: { name: string; kind?: "manual" | "smart"; rules?: Record<string, string>; asset_ids?: number[] }) => request<{ id: number }>("POST", `/repos/${id}/albums`, { body }),
  updateAlbum: (id: string, aid: number, body: { name?: string; rules?: Record<string, string> }) => request("PATCH", `/repos/${id}/albums/${aid}`, { body }),
  deleteAlbum: (id: string, aid: number) => request("DELETE", `/repos/${id}/albums/${aid}`),
  albumAdd: (id: string, aid: number, asset_ids: number[]) => request<{ added: number }>("POST", `/repos/${id}/albums/${aid}/items`, { body: { asset_ids } }),
  albumRemove: (id: string, aid: number, asset_ids: number[]) => request<{ removed: number }>("POST", `/repos/${id}/albums/${aid}/remove`, { body: { asset_ids } }),
  editDate: (id: string, asset_ids: number[], body: { shift_seconds?: number; value?: string }) => request<{ updated: number }>("POST", `/repos/${id}/edits/date`, { body: { asset_ids, ...body } }),
  editLocation: (id: string, asset_ids: number[], lat: number, lon: number) => request<{ updated: number; place: string; region: string; job: Job | null }>("POST", `/repos/${id}/edits/location`, { body: { asset_ids, lat, lon } }),
  geoSearch: (q: string, online: boolean, near?: { lat: number; lon: number } | null) => request<{ results: { label: string; sub: string; lat: number; lon: number; source: "offline" | "online" }[] }>("GET", "/geo/search", { query: { q, online: online ? 1 : 0, lat: near?.lat, lon: near?.lon }, timeout: 8_000 }),
  revertEdits: (id: string, asset_ids: number[]) => request<{ reverted: number; job: Job | null }>("POST", `/repos/${id}/edits/revert`, { body: { asset_ids } }),
  exportFiles: (id: string, body: { file_ids: number[]; dest: string; size: string; strip_location: boolean }) => request<Job>("POST", `/repos/${id}/export`, { body }),
  devices: () => request<{ devices: DeviceInfo[] }>("GET", "/devices", { timeout: 15_000 }),
  deviceItems: (device: string, repo: string) => request<{ device: DeviceInfo; items: DeviceItem[] }>("GET", "/devices/items", { query: { device, repo }, timeout: 200_000 }),
  importItems: (repo: string, body: { device: string; keys: string[]; template: string; delete_after: boolean }) => request<Job>("POST", `/repos/${repo}/import`, { body }),
  compressVideos: (id: string, file_ids: number[]) => request<Job>("POST", `/repos/${id}/compress`, { body: { file_ids } }),
  mapPoints: (id: string) => request<{ points: MapPoint[] }>("GET", `/repos/${id}/map`, { timeout: 60_000 }),
  folders: (id: string, path: string, scope = "all") => request<FolderList>("GET", `/repos/${id}/folders`, { query: { path, scope } }),
  facets: (id: string, scope: string) => request<Facets>("GET", `/repos/${id}/facets`, { query: { scope } }),
  asset: (id: string, aid: number) => request<AssetDetail>("GET", `/repos/${id}/assets/${aid}`),
  updateAsset: (id: string, aid: number, body: { rating?: number; favorite?: boolean }) => request("PATCH", `/repos/${id}/assets/${aid}`, { body }),
  dups: (id: string, query: Query) => request<DupsResponse>("GET", `/repos/${id}/dups`, { query, timeout: 180_000 }),
  organizePreview: (id: string, query: Query) => request<OrganizePreview>("GET", `/repos/${id}/organize/preview`, { query, timeout: 60_000 }),
  createPlan: (id: string, body: Record<string, unknown>) => request<Plan>("POST", `/repos/${id}/plans`, { body: { source: "ui", ...body } }),
  plans: (id: string) => request<Plan[]>("GET", `/repos/${id}/plans`),
  plan: (id: string, pid: number, limit = 500) => request<Plan>("GET", `/repos/${id}/plans/${pid}`, { query: { limit } }),
  applyPlan: (id: string, pid: number) => request<Job>("POST", `/repos/${id}/plans/${pid}/apply`),
  undoPlan: (id: string, pid: number) => request<Job>("POST", `/repos/${id}/plans/${pid}/undo`),
  rejectPlan: (id: string, pid: number) => request<Plan>("POST", `/repos/${id}/plans/${pid}/reject`),
  setTags: (id: string, aid: number, add: string[], remove: string[]) => request<{ tags: string[] }>("POST", `/repos/${id}/assets/${aid}/tags`, { body: { add, remove } }),
  settings: () => request<AppSettings>("GET", "/settings"),
  updateSettings: (patch: Record<string, unknown>) => request<AppSettings>("PATCH", "/settings", { body: patch }),
  setApiKey: (key: string, provider: string) => request<AppSettings>("PUT", "/settings/api-key", { body: { key, provider } }),
  deleteApiKey: (provider: string) => request<AppSettings>("DELETE", "/settings/api-key", { query: { provider } }),
  models: (provider: string) => request<{ provider: string; models: { id: string; name: string }[] }>("GET", "/settings/models", { query: { provider }, timeout: 60_000 }),
  chat: (id: string, message: string, conversation_id: string | null, context: Record<string, unknown>) =>
    request<ChatResult>("POST", `/repos/${id}/assistant`, { body: { message, conversation_id, context } }),
  resetChat: (cid: string) => request("DELETE", `/assistant/${cid}`),
  modelTiers: (repoId?: string | null) => request<TierStatus>("GET", "/models/tiers", { query: { repo_id: repoId ?? undefined } }),
  tierTry: (kind: TierKind, tier: Tier, repo_id: string) => request<Job>("POST", "/models/tiers/try", { body: { kind, tier, repo_id } }),
  tierApply: (kind: TierKind, tier: Tier) => request<TierStatus>("POST", "/models/tiers/apply", { body: { kind, tier } }),
  tierRemove: (kind: TierKind, tier: Tier) => request<TierStatus>("POST", "/models/tiers/remove", { body: { kind, tier } }),
  mlModels: () => request<{ models: ModelStatus[]; root: string }>("GET", "/models"),
  downloadModels: (name?: string) => request<Job>("POST", "/models/download", { body: { name: name ?? null } }),
  smart: (id: string) => request<SmartState>("GET", `/repos/${id}/smart`),
  smartRun: (id: string, force = false) => request<Job>("POST", `/repos/${id}/smart/run`, { body: { force } }),
  similar: (id: string, aid: number) => request<{ items: AssetItem[] }>("GET", `/repos/${id}/assets/${aid}/similar`),
  people: (id: string) => request<{ persons: Person[]; clusters: FaceCluster[]; pet_clusters?: FaceCluster[] }>("GET", `/repos/${id}/people`),
  nameCluster: (id: string, cid: number, name: string) => request("POST", `/repos/${id}/people/clusters/${cid}/name`, { body: { name } }),
  assignFace: (id: string, fid: number, name: string | null) => request("POST", `/repos/${id}/faces/${fid}/assign`, { body: { name } }),
  recluster: (id: string) => request<Job>("POST", `/repos/${id}/people/recluster`),
  videoInfo: (id: string, aid: number) => request<VideoInfo>("GET", `/repos/${id}/assets/${aid}/video`),
  transcript: (id: string, aid: number) => request<{ lang: string | null; segments: { start_ms: number; end_ms: number; text: string }[] }>("GET", `/repos/${id}/assets/${aid}/transcript`),
  makeProxy: (id: string, blake: string) => request<Job>("POST", `/repos/${id}/proxy/${blake}`),
  moments: (id: string, aid: number, q: string) => request<{ moments: { ms: number; end_ms: number; score: number }[] }>("GET", `/repos/${id}/assets/${aid}/moments`, { query: { q }, timeout: 90_000 }),
  snapshot: (id: string, aid: number, ms: number) => request<Job>("POST", `/repos/${id}/assets/${aid}/snapshot`, { body: { ms } }),
  clip: (id: string, aid: number, start_ms: number, end_ms: number) => request<Job>("POST", `/repos/${id}/assets/${aid}/clip`, { body: { start_ms, end_ms } }),
  tagPerson: (id: string, aid: number, name: string, box?: [number, number, number, number]) =>
    request<{ face_id: number; person_id: number; detected: boolean ; animal?: string }>("POST", `/repos/${id}/assets/${aid}/people`, { body: { name, box: box ?? null } }),
  deleteFace: (id: string, fid: number) => request("DELETE", `/repos/${id}/faces/${fid}`),
  excludeFace: (id: string, fid: number, reason: "removed" | "not_face" | null) => request("POST", `/repos/${id}/faces/${fid}/exclude`, { body: { reason } }),
  rejectSuggestion: (id: string, cid: number, person_id: number) => request<{ faces: number }>("POST", `/repos/${id}/people/clusters/${cid}/reject`, { body: { person_id } }),
  dissolveCluster: (id: string, cid: number) => request<{ faces: number }>("POST", `/repos/${id}/people/clusters/${cid}/dissolve`),
  ignoreCluster: (id: string, cid: number) => request<{ faces: number }>("POST", `/repos/${id}/people/clusters/${cid}/ignore`),
  ignoredPeople: (id: string) => request<{ groups: IgnoredGroup[]; not_faces: { face_id: number; asset_id: number }[] }>("GET", `/repos/${id}/people/ignored`),
  restoreIgnored: (id: string, group: number) => request<{ faces: number }>("POST", `/repos/${id}/people/ignored/${group}/restore`),
  renamePerson: (id: string, pid: number, name: string) => request("PATCH", `/repos/${id}/people/${pid}`, { body: { name } }),
  mergeSuggestions: (id: string) => request<{ suggestions: { into: MiniPerson; other: MiniPerson; similarity: number }[] }>("GET", `/repos/${id}/people/merge-suggestions`),
  dismissMerge: (id: string, a: number, b: number) => request("POST", `/repos/${id}/people/merge-suggestions/dismiss`, { body: { a, b } }),
  mergePerson: (id: string, pid: number, into: number) => request("POST", `/repos/${id}/people/${pid}/merge`, { body: { into } }),
  personFaces: (id: string, pid: number, limit = 400) => request<{ id: number; asset_id: number; quality: number; frame_ms: number | null; taken_at: string | null }[]>("GET", `/repos/${id}/people/${pid}/faces`, { query: { limit } }),
  personTimeline: (id: string, pid: number) => request<{ person: { id: number; name: string; kind: string; birth_date: string | null }; faces: { id: number; asset_id: number; quality: number; taken_at: string }[] }>("GET", `/repos/${id}/people/${pid}/timeline`),
  setPersonBirth: (id: string, pid: number, birth_date: string | null) => request<{ birth_date: string | null }>("PUT", `/repos/${id}/people/${pid}/birth`, { body: { birth_date } }),
  geoSuggest: (id: string, gpx?: string) => request<GeoSuggestions>("POST", `/repos/${id}/geo/suggest`, { body: { gpx: gpx ?? null }, timeout: 90_000 }),
  geoApply: (id: string, items: { asset_id: number; lat: number; lon: number }[]) => request<{ updated: number; job: Job | null }>("POST", `/repos/${id}/geo/apply`, { body: { items } }),
  geoSkip: (id: string, asset_ids: number[]) => request<{ skipped: number }>("POST", `/repos/${id}/geo/skip`, { body: { asset_ids } }),
  geoClearSkips: (id: string) => request<{ cleared: number }>("DELETE", `/repos/${id}/geo/skip`),
  setPersonCover: (id: string, pid: number, face_id: number | null) => request("PUT", `/repos/${id}/people/${pid}/cover`, { body: { face_id } }),
  deletePerson: (id: string, pid: number) => request("DELETE", `/repos/${id}/people/${pid}`),
  trash: (id: string) => request<TrashInfo>("GET", `/repos/${id}/trash`),
  restoreTrash: (id: string, item_ids: number[]) => request<{ restored: number; skipped: number; failed: number }>("POST", `/repos/${id}/trash/restore`, { body: { item_ids } }),
  deleteTrash: (id: string, item_ids: number[]) => request<{ deleted: number; bytes: number }>("POST", `/repos/${id}/trash/delete`, { body: { item_ids } }),
  emptyTrash: (id: string, older_than?: number) => request<{ deleted: number; bytes: number }>("POST", `/repos/${id}/trash/empty`, { body: { older_than: older_than ?? null } }),
};

/** Bir işin bitmesini bekler. */
export async function waitJob(jid: number, onProgress?: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api.job(jid);
    onProgress?.(j);
    if (j.status === "done") return j;
    if (j.status === "failed") throw new Error(j.error ?? "İş başarısız oldu");
    if (j.status === "cancelled") throw new Error("İş durduruldu");
    await new Promise((r) => setTimeout(r, 400));
  }
}
