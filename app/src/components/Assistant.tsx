import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, thumbUrl } from "../api";
import { useApp, useSummary } from "../state";
import type { AssetItem, ChatEvent, ChatResult, Plan } from "../types";
import { cls, fmtBytes, fmtInt } from "../util";
import { Markdown } from "./Markdown";
import { ArrowUp, Check, Close, Compose, Sparkles } from "./Icons";

type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; events: ChatEvent[]; plans: Plan[]; files: ChatResult["files"] }
  | { role: "error"; text: string };

const TOOL_LABEL: Record<string, string> = {
  list_repos: "Kütüphanelere baktı", repo_status: "Kütüphaneye baktı", search_media: "Aradı", get_media: "Ayrıntılara baktı", list_facets: "Arşivi özetledi",
  list_people: "Kişilere baktı", get_video_details: "Videoyu inceledi", find_duplicates: "Tekrarları buldu", preview_organize: "Düzenlemeyi denedi",
  update_media: "Etiketledi", create_plan: "Değişiklik önerdi", apply_plan: "Uyguladı", undo_plan: "Geri aldı", list_plans: "Geçmişe baktı", get_preview_image: "Fotoğrafa baktı",
  list_albums: "Albümlere baktı", create_album: "Albüm oluşturdu", add_to_album: "Albümü güncelledi", list_memories: "Anılara baktı",
  cleanup_suggestions: "Temizlik önerilerine baktı", fix_dates: "Tarihi düzeltti", set_location: "Konum verdi",
};

const PAGE_LABEL: Record<string, string> = {
  library: "Kütüphane", places: "Yerler", dups: "Tekrarlar", organize: "Klasörleri düzenle", history: "Geçmiş", trash: "Son silinenler", settings: "Ayarlar",
  assistant: "Asistan ayarları", people: "Kişiler", memories: "Anılar", cleanup: "Temizlik", import: "İçe aktar",
};

const STATUS: Record<string, string> = { pending: "Onayını bekliyor", applied: "Yapıldı", rejected: "Reddedildi", undone: "Geri alındı", partial: "Kısmen uygulandı", running: "Uygulanıyor" };

function PlanCard({ repoId, plan }: { repoId: string; plan: Plan }) {
  const { showPlan, toast } = useApp();
  const qc = useQueryClient();
  const live = useQuery({ queryKey: ["plan", repoId, plan.id, "card"], queryFn: () => api.plan(repoId, plan.id, 0), initialData: plan, refetchInterval: 8000 }).data;
  const pending = live.status === "pending";
  return (
    <div style={{ borderRadius: 14, background: "var(--panel)", boxShadow: "var(--shadow-sm)", padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="row"><b className="grow">{live.title || "Değişiklik önerisi"}</b>
        <span className={cls("status", `st-${live.status}`)}>{STATUS[live.status] ?? live.status}</span></div>
      <span className="muted" style={{ fontSize: 12 }}>{fmtInt(live.summary.files)} dosya · {fmtBytes(live.summary.bytes)}</span>
      {pending && (
        <div className="row wrap" style={{ marginTop: 4 }}>
          <button className="btn sm" onClick={async () => { await api.rejectPlan(repoId, plan.id); qc.invalidateQueries(); toast("Öneri reddedildi"); }}>Reddet</button>
          <button className="btn sm pri" onClick={async () => showPlan(await api.plan(repoId, plan.id), () => qc.invalidateQueries())}>İncele…</button>
        </div>
      )}
    </div>
  );
}

function fileToItem(f: ChatResult["files"][number]): AssetItem {
  return {
    file_id: f.file_id, rel_path: f.rel_path, tracked: 1, size: 0, asset_id: f.asset_id, blake3: f.blake3, kind: f.kind, mime: null,
    width: null, height: null, duration_ms: f.duration_ms, taken_at: f.taken_at, taken_at_source: null, camera_model: null, rating: 0,
    favorite: 0, lat: null, lon: null, place: f.place, region: f.region, country_code: null, copies: 1, in_dup: 0,
  };
}

function KeySetup({ onDone, provider, label, prefix }: { onDone: () => void; provider: string; label: string; prefix: string }) {
  const { toast, setPage } = useApp();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api.setApiKey(key, provider);
      setKey("");
      onDone();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
    setBusy(false);
  };
  return (
    <div className="col" style={{ padding: 16, gap: 12 }}>
      <b>Asistanı kullanmak için {label} anahtarı gerekiyor</b>
      <span className="muted" style={{ fontSize: 13 }}>Anahtar macOS Anahtar Zinciri'nde saklanır. Sağlayıcıyı Ayarlar › Asistan'dan değiştirebilirsin.</span>
      <label className="field"><span className="sr">API anahtarı</span><input type="password" placeholder={prefix ? `${prefix}…` : "API anahtarı"} value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => e.key === "Enter" && key && save()} /></label>
      <div className="row"><button className="btn pri" disabled={!key || busy} onClick={save}>Kaydet</button><button className="btn ghost" onClick={() => setPage("assistant")}>Ayarlar</button></div>
      <span className="muted" style={{ fontSize: 12 }}>Ya da MediaMind'ı Claude Desktop veya Claude Code'a bağlayabilirsin (Ayarlar › Gizlilik ve bağlantılar).</span>
    </div>
  );
}

export function AssistantDrawer({ hidden }: { hidden: boolean }) {
  const { repoId, toggleAssistant, page, filter, view, selection, openViewer, setPage } = useApp();
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const { data: summary } = useSummary(repoId);
  const [threads, setThreads] = useState<Record<string, { cid: string | null; msgs: Msg[] }>>({});
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const thread = repoId ? threads[repoId] ?? { cid: null, msgs: [] } : { cid: null, msgs: [] };

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [thread.msgs.length, busy]);
  useEffect(() => {
    if (!hidden) inputRef.current?.focus();
  }, [hidden]);

  const push = (m: Msg, cid?: string | null) =>
    setThreads((t) => {
      const cur = t[repoId!] ?? { cid: null, msgs: [] };
      return { ...t, [repoId!]: { cid: cid === undefined ? cur.cid : cid, msgs: [...cur.msgs, m] } };
    });

  const send = async (msg?: string) => {
    const q = (msg ?? text).trim();
    if (!q || !repoId || busy) return;
    setText("");
    push({ role: "user", text: q });
    setBusy(true);
    try {
      const ctx = { page: PAGE_LABEL[page] ?? page, view, filter, selected_file_ids: selection };
      const r = await api.chat(repoId, q, thread.cid, ctx);
      push({ role: "assistant", text: r.reply, events: r.events, plans: r.plans, files: r.files }, r.conversation_id);
      const WRITES = ["update_media", "create_plan", "create_album", "add_to_album", "fix_dates", "set_location"];
      if (r.events.some((e) => WRITES.includes(e.name))) qc.invalidateQueries();
    } catch (e) {
      push({ role: "error", text: String((e as Error).message) });
    }
    setBusy(false);
  };

  const reset = async () => {
    if (thread.cid) api.resetChat(thread.cid).catch(() => undefined);
    setThreads((t) => ({ ...t, [repoId!]: { cid: null, msgs: [] } }));
  };

  const hasKey = !!settings.data?.api_key;
  const active = settings.data?.active;
  const provLabel = active ? settings.data!.providers[active.provider].label : "";
  const modelName = !active ? "" : active.provider === "anthropic" ? settings.data!.models[active.model] : `${provLabel} · ${active.model || "model seçilmedi"}`;
  const ctxText = [PAGE_LABEL[page] ?? page, selection.length ? `${selection.length} seçili` : null, filter.year || filter.place || filter.q || null].filter(Boolean).join(" · ");
  const agentPlans = (summary?.pending_plan_list ?? []).filter((p) => p.source.startsWith("mcp"));

  return (
    <aside className="drawer" aria-label="Asistan" style={{ display: hidden ? "none" : "flex" }}>
      <div className="row" data-tauri-drag-region style={{ height: 52, flex: "none", padding: "0 8px 0 16px", borderBottom: "1px solid var(--line)" }}>
        <b className="grow" data-tauri-drag-region style={{ fontSize: 13 }}>Asistan</b>
        {thread.msgs.length > 0 && <button className="ibtn" onClick={reset} aria-label="Yeni sohbet" title="Yeni sohbet"><Compose size={16} /></button>}
        <button className="ibtn" onClick={toggleAssistant} aria-label="Asistanı kapat" title="Kapat (⌘J)"><Close size={15} /></button>
      </div>

      {!repoId ? <div className="empty">Önce bir kütüphane ekle.</div> : !settings.data ? null : !hasKey ? (
        <KeySetup onDone={() => settings.refetch()} provider={active!.provider} label={provLabel} prefix={settings.data.providers[active!.provider].key_prefix} />
      ) : !active?.model ? (
        <div className="col" style={{ padding: 16, gap: 10 }}>
          <b>{provLabel} için model seçilmedi</b>
          <span className="muted" style={{ fontSize: 13 }}>Ayarlar › Asistan bölümünden bir model seç.</span>
          <button className="btn pri" style={{ alignSelf: "flex-start" }} onClick={() => setPage("assistant")}>Ayarlara git</button>
        </div>
      ) : (
        <>
          <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 14 }} className="selectable">
            {agentPlans.length > 0 && (
              <div className="banner warn" style={{ fontSize: 13 }}>
                <span className="grow">Bağlı bir uygulama {agentPlans.length} değişiklik önerdi.</span>
                <button className="btn sm" onClick={() => setPage("history")}>Göster</button>
              </div>
            )}
            {thread.msgs.length === 0 && (
              <div className="col" style={{ gap: 8, alignItems: "center", textAlign: "center", padding: "24px 4px" }}>
                <Sparkles size={28} style={{ color: "var(--accent)" }} />
                <b>Fotoğrafların hakkında bir şey sor</b>
                <span className="muted" style={{ fontSize: 12 }}>Taşıma ve silme gibi değişiklikler önce sana sorulur.</span>
                <div className="col" style={{ gap: 6, marginTop: 8, alignItems: "stretch", width: "100%" }}>
                  {["Arşivde hangi yıllar ve yerler var?", "2024 yazında çekilmiş videoları bul", "WhatsApp kopyalarını temizle", "Tarihi olmayan dosyalar hangileri?"].map((s) => (
                    <button key={s} className="suggest" onClick={() => send(s)}>{s}</button>
                  ))}
                </div>
              </div>
            )}
            {thread.msgs.map((m, i) => m.role === "user" ? (
              <div key={i} className="bubble-u">{m.text}</div>
            ) : m.role === "error" ? (
              <div key={i} className="banner warn" style={{ fontSize: 13 }}>{m.text}</div>
            ) : (
              <div key={i} className="col" style={{ gap: 8 }}>
                {m.events.length > 0 && (
                  <div className="muted" style={{ fontSize: 11, display: "flex", flexWrap: "wrap", gap: "2px 10px" }}>
                    {m.events.map((e, k) => (
                      <span key={k} className="row" style={{ gap: 3 }} title={e.error ?? e.summary ?? ""}>
                        {e.error ? <Close size={11} style={{ color: "var(--danger)" }} /> : <Check size={11} />}{TOOL_LABEL[e.name] ?? e.name}
                      </span>
                    ))}
                  </div>
                )}
                {m.text && <div className="bubble-a md-bubble"><Markdown text={m.text} /></div>}
                {m.files.length > 0 && (
                  <div className="row wrap" style={{ gap: 4 }}>
                    {m.files.slice(0, 12).map((f, k) => (
                      <button key={f.file_id} onClick={() => openViewer(m.files.map(fileToItem), k)} aria-label={f.rel_path} title={f.rel_path}
                        style={{ width: 56, height: 56, padding: 0, border: 0, borderRadius: 6, overflow: "hidden", cursor: "pointer", background: "var(--tile-bg)" }}>
                        <img src={thumbUrl(repoId!, f.blake3, "s")} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} loading="lazy" />
                      </button>
                    ))}
                    {m.files.length > 12 && <span className="muted" style={{ fontSize: 12 }}>+{m.files.length - 12}</span>}
                  </div>
                )}
                {m.plans.map((p) => <PlanCard key={p.id} repoId={repoId!} plan={p} />)}
              </div>
            ))}
            {busy && <div className="bubble-a typing" aria-label="Yazıyor"><i /><i /><i /></div>}
          </div>
          <div style={{ padding: "10px 12px 12px" }}>
            <div className="row" style={{ alignItems: "flex-end", borderRadius: 18, background: "var(--fill)", padding: "5px 5px 5px 12px", gap: 6 }}>
              <label className="grow" style={{ display: "flex" }}>
                <span className="sr">Asistana yaz</span>
                <textarea ref={inputRef} rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Sor ya da komut ver…"
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                  style={{ flex: 1, resize: "none", background: "transparent", border: 0, outline: "none", padding: "4px 0", maxHeight: 120, fieldSizing: "content" } as React.CSSProperties} />
              </label>
              <button className="send" onClick={() => send()} disabled={busy || !text.trim()} aria-label="Gönder"><ArrowUp size={14} /></button>
            </div>
            <div className="muted ellipsis" style={{ marginTop: 6, fontSize: 10.5, textAlign: "center" }} title={ctxText}>{modelName}</div>
          </div>
        </>
      )}
    </aside>
  );
}
