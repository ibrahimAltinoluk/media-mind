export interface Counts {
  tracked: number;
  untracked: number;
  missing: number;
  trashed: number;
  photos: number;
  videos: number;
  bytes: number;
}

export interface RepoEntry {
  id: string;
  name: string;
  last_path: string;
  last_seen: string;
  volume_name: string | null;
  online: boolean;
  counts?: Counts;
}

export interface Summary {
  repo: RepoEntry;
  counts: Counts;
  analysis_pending: number;
  dup_files: number;
  trash_files: number;
  trash_bytes: number;
  pending_plans: number;
  pending_plan_list: { id: number; kind: string; title: string | null; source: string; summary: Plan["summary"]; created_at: string }[];
  busy: boolean;
  config: {
    name: string;
    dedup: { keep_rule: string; primary_folders: string[]; near_threshold: number };
    scan: { auto_track: boolean; watch?: boolean };
    analysis: Record<string, boolean>;
    trash?: { auto_days: number };
  };
}

export interface Probe {
  path: string;
  name: string;
  is_repo: boolean;
  inside_repo: string | null;
  photos: number;
  videos: number;
  bytes: number;
  complete: boolean;
}

export interface Job {
  id: number;
  type: string;
  repo_id: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  lane: string;
  stage: string;
  done: number;
  total: number;
  result: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  error: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  cancel_requested: boolean;
}

export interface AssetItem {
  file_id: number;
  rel_path: string;
  tracked: number;
  size: number;
  asset_id: number;
  blake3: string;
  kind: "photo" | "video";
  mime: string | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  taken_at: string | null;
  taken_at_source: string | null;
  camera_model: string | null;
  rating: number;
  favorite: number;
  lat: number | null;
  lon: number | null;
  place: string | null;
  region: string | null;
  country_code: string | null;
  copies: number;
  in_dup: number;
  match?: "text" | "visual";
}

export interface AssetDetail {
  id: number;
  blake3: string;
  size: number;
  kind: "photo" | "video";
  mime: string | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  taken_at: string | null;
  taken_at_source: string | null;
  tz_offset: string | null;
  lat: number | null;
  lon: number | null;
  alt: number | null;
  camera_make: string | null;
  camera_model: string | null;
  lens: string | null;
  video_codec: string | null;
  bitrate: number | null;
  blur_score: number | null;
  rating: number;
  favorite: number;
  place: string | null;
  region: string | null;
  district: string | null;
  country_code: string | null;
  analysis_state: { errors?: Record<string, string> } & Record<string, unknown>;
  files: { id: number; rel_path: string; size: number; state: string; tracked: number; first_seen: string }[];
  tags: string[];
  model_tags: { name: string; kind: string; confidence: number }[];
  faces: FaceBox[];
  text: string | null;
  dup_group: { id: number; kind: string; n: number } | null;
}

export interface Facets {
  years: { year: string; n: number }[];
  places: { place: string; region: string | null; n: number }[];
  cameras: { camera: string; n: number }[];
  kinds: { kind: string; n: number }[];
  tags: { tag: string; n: number }[];
  people: { id: number; name: string; n: number }[];
}

export interface DupMember {
  file_id: number;
  asset_id: number;
  rel_path: string;
  size: number;
  kind: string;
  width: number | null;
  height: number | null;
  taken_at: string | null;
  role: "keep" | "delete" | "optional";
  reason: string;
  blake3: string;
  tracked: boolean;
}

export interface DupGroup {
  id: number;
  kind: "exact" | "near" | "video" | "burst";
  gain_bytes: number;
  members: DupMember[];
}

export interface DupsResponse {
  groups: DupGroup[];
  totals: Record<"exact" | "near" | "video" | "burst", { groups: number; files: number; bytes: number }>;
}

export interface PlanItem {
  id: number;
  seq: number;
  op: "move" | "trash";
  file_id: number | null;
  src: string;
  dst: string;
  size: number;
  status: string;
  error: string | null;
}

export interface Plan {
  id: number;
  kind: string;
  source: string;
  status: string;
  title: string | null;
  summary: { files?: number; items?: number; bytes?: number; renamed?: number };
  created_at: string;
  applied_at: string | null;
  undone_at: string | null;
  items?: PlanItem[];
  item_count?: number;
}

export interface OrganizePreview {
  template: string;
  total: number;
  unchanged: number;
  moves: { file_id: number; src: string; dst: string }[];
  tree: { folder: string; n: number }[];
  presets: Record<string, string>;
}

export interface TrashInfo {
  files: number;
  bytes: number;
  items: TrashItem[];
}

export type Provider = "anthropic" | "openrouter" | "gemini";

export interface AppSettings {
  settings: {
    assistant: { provider: Provider; model: string; effort: "low" | "medium" | "high"; openrouter_model: string; gemini_model: string };
    agents: { read: boolean; tag: boolean; require_approval: boolean; send_previews: boolean };
  };
  models: Record<string, string>;
  providers: Record<Provider, { label: string; key_url: string; key_prefix: string }>;
  api_keys: Record<Provider, string | null>;
  api_key: string | null;
  active: { provider: Provider; model: string };
  mcp: { command: string; config: unknown; claude_code: string; tools: { name: string; description: string }[] };
}

export interface ChatEvent {
  type: "tool";
  name: string;
  input: Record<string, unknown>;
  summary?: string;
  error?: string;
}

export interface ChatResult {
  conversation_id: string;
  reply: string;
  events: ChatEvent[];
  plans: Plan[];
  files: { file_id: number; asset_id: number; blake3: string; kind: "photo" | "video"; rel_path: string; taken_at: string | null; duration_ms: number | null; place: string | null; region: string | null }[];
  provider: Provider;
  model: string;
}

export interface FaceBox {
  id: number;
  x1: number; y1: number; x2: number; y2: number;
  score: number;
  quality: number;
  person_id: number | null;
  cluster_id: number | null;
  assigned_by: string | null;
  manual: number;
  frame_ms: number | null;
  name: string | null;
}

export interface ModelStatus {
  name: string;
  label: string;
  repo: string;
  license: string;
  purpose: string;
  size: number;
  downloaded: number;
  installed: boolean;
}

export interface SmartState {
  total: number;
  content: number;
  faces: number;
  faces_found: number;
  content_model: boolean;
  face_model: boolean;
  videos: number;
  videos_done: number;
  transcript_model: boolean;
  transcripts_done: number;
  enabled: { content: boolean; faces: boolean; video: boolean; transcripts: boolean; ocr: boolean; pets: boolean };
  pet_model: boolean;
  pets_found: number;
  ocr_model: boolean;
  ocr_done: number;
  with_text: number;
}

export interface Person {
  id: number;
  name: string;
  kind: string;
  photos: number;
  faces: number;
  cover: number | null;
  cover_custom?: boolean;
  hidden: number;
}

export interface FaceCluster {
  cluster_id: number;
  faces: number;
  photos: number;
  sample_faces: number[];
  members: { face_id: number; asset_id: number }[];
  suggestion: { person_id: number; name: string; similarity: number } | null;
  animal?: string;
}

export interface VideoInfo {
  scenes: { idx: number; start_ms: number; end_ms: number; key_ms: number }[];
  people: { person_id: number; name: string; segments: [number, number][] }[];
  unknown_faces: number;
  analyzed: boolean;
  playable: boolean;
  proxy_ready: boolean;
}

export interface FolderEntry { name: string; path: string; count: number; bytes: number; new: number; covers: string[]; latest: string | null }
export interface FolderList { path: string; folders: FolderEntry[]; files: number; total: number }

export interface TrashItem {
  id: number; plan_id: number; src: string; size: number; applied_at: string;
  file_id: number; tracked: number; asset_id: number; blake3: string; kind: "photo" | "video"; mime: string | null;
  width: number | null; height: number | null; duration_ms: number | null; taken_at: string | null; favorite: number;
}

export interface Album { id: number; name: string; kind: "manual" | "smart"; rules: Record<string, string> | null; count: number | null; cover: string | null }
export interface Memory { key: string; kind: "on_this_day" | "trip" | "day"; title: string; subtitle: string; count: number; date_from: string; date_to: string; cover: string[] }
export interface CleanupCategory { key: string; title: string; hint: string; count: number; bytes: number; items: AssetItem[] }
