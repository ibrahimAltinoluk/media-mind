import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, backendUrl } from "../api";
import { showOnboarding } from "../components/Onboarding";
import { ModelQuality } from "../components/ModelQuality";
import { Toolbar } from "../components/Toolbar";
import { useApp, useSummary, type Theme } from "../state";
import type { Plan } from "../types";
import { cls, fmtBytes, fmtDate, fmtInt } from "../util";

type Tab = "general" | "smart" | "assistant" | "mcp";

/** System Settings tarzı satır: solda başlık + açıklama, sağda kontrol. */
export function SRow({ label, hint, children }: { label: React.ReactNode; hint?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="grow-row">
      <div className="k">{label}{hint && <small>{hint}</small>}</div>
      {children}
    </div>
  );
}

export function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <label className="switch" title={label}>
      <input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

function Section({ title, children, foot }: { title?: string; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <section>
      {title && <div className="group-title">{title}</div>}
      <div className="group">{children}</div>
      {foot && <div className="muted" style={{ fontSize: 11.5, margin: "6px 4px 0", lineHeight: 1.45 }}>{foot}</div>}
    </section>
  );
}

/* ---------------- Genel ---------------- */

function General() {
  const { repoId, theme, setTheme, toast, trackJob } = useApp();
  const qc = useQueryClient();
  const { data: s } = useSummary(repoId);
  const app = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const writeLoc = app.data?.settings.files?.write_location ?? true;
  const [name, setName] = useState<string | null>(null);
  const [primary, setPrimary] = useState<string | null>(null);
  const save = async (patch: Record<string, unknown>) => {
    await api.updateConfig(repoId!, patch);
    qc.invalidateQueries({ queryKey: ["summary", repoId] });
  };
  const THEMES: [Theme, string][] = [["system", "Otomatik"], ["light", "Açık"], ["dark", "Koyu"]];
  return (
    <>
      <Section title="Görünüm" foot="Görüntüleyici, fotoğrafları öne çıkarmak için her zaman koyu açılır.">
        <SRow label="Tema">
          <div className="seg">
            {THEMES.map(([t, l]) => <button key={t} className={cls(theme === t && "on")} onClick={() => setTheme(t)}>{l}</button>)}
          </div>
        </SRow>
      </Section>
      <Section title="Dosyalar" foot="Konum dosyanın içindeki bilgilere (EXIF) yazılır; fotoğrafın kendisi, çekim tarihi ve dosyanın oluşturulma/değiştirilme tarihleri değişmez. Böylece Fotoğraflar, Google Fotoğraflar gibi başka uygulamalar da konumu görür.">
        <SRow label="Eklediğim konumu dosyaya da yaz" hint="Kapalıysa konum yalnız MediaMind'da tutulur, dosyalara dokunulmaz.">
          <Switch label="Konumu dosyaya yaz" checked={writeLoc} onChange={async (v) => {
            try { qc.setQueryData(["settings"], await api.updateSettings({ files: { write_location: v } })); }
            catch (e) { toast(String((e as Error).message), { error: true }); }
          }} />
        </SRow>
      </Section>
      {s && (
        <Section title="Bu kütüphane" foot={`${fmtInt(s.counts.photos)} fotoğraf, ${fmtInt(s.counts.videos)} video · ${fmtBytes(s.counts.bytes)}`}>
          <SRow label="Ad">
            <div className="field" style={{ width: 220 }}>
              <input value={name ?? s.repo.name} onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                onBlur={async () => {
                  if (name && name !== s.repo.name) { await api.renameRepo(repoId!, name); qc.invalidateQueries(); toast("Ad değiştirildi"); }
                  setName(null);
                }} />
            </div>
          </SRow>
          <SRow label="Konum"><span className="mono selectable ellipsis" style={{ maxWidth: 320 }} title={s.repo.last_path}>{s.repo.last_path}</span></SRow>
          <SRow label="Klasördeki değişiklikleri izle" hint="Finder'da eklenen, silinen ya da taşınan dosyalar birkaç saniye içinde fark edilir.">
            <Switch label="Klasördeki değişiklikleri izle" checked={s.config.scan.watch !== false} onChange={(v) => save({ scan: { watch: v } })} />
          </SRow>
          <SRow label="Yeni dosyaları kendiliğinden ekle" hint="Kapalıysa yeni dosyalar “Yeni eklenenler”de onayını bekler.">
            <Switch label="Yeni dosyaları kendiliğinden ekle" checked={s.config.scan.auto_track} onChange={(v) => save({ scan: { auto_track: v } })} />
          </SRow>
          <SRow label="Son silinenleri kendiliğinden temizle" hint="Bu süreden eski silinenler kalıcı olarak silinir (kütüphane her tarandığında).">
            <select className="sel" value={String(s.config.trash?.auto_days ?? 0)} onChange={(e) => save({ trash: { auto_days: Number(e.target.value) } })} aria-label="Son silinenleri temizleme süresi">
              <option value="0">Kapalı</option><option value="30">30 gün sonra</option><option value="60">60 gün sonra</option><option value="90">90 gün sonra</option>
            </select>
          </SRow>
          <SRow label="Ana klasörler" hint="Tekrarlarda kalite eşitse bu klasörlerdeki kopya tutulur. Virgülle ayır.">
            <div className="field" style={{ width: 220 }}>
              <input value={primary ?? s.config.dedup.primary_folders.join(", ")} placeholder="ör. DCIM, Kamera" onChange={(e) => setPrimary(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                onBlur={async () => {
                  if (primary !== null) { await save({ dedup: { primary_folders: primary.split(",").map((x) => x.trim()).filter(Boolean) } }); toast("Kaydedildi"); }
                  setPrimary(null);
                }} />
            </div>
          </SRow>
          <SRow label="Tarih ve yer bilgilerini yeniden oku" hint="Dosyaların bilgilerini baştan okur. Etiketlerin ve kişilerin korunur.">
            <button className="btn" onClick={async () => trackJob(await api.analyze(repoId!, true))}>Yeniden oku</button>
          </SRow>
        </Section>
      )}
      <Section title="Yardım">
        <SRow label="Tanıtımı yeniden göster" hint="MediaMind'ın ne yapabildiğini 5 adımda anlatır.">
          <button className="btn" onClick={showOnboarding}>Göster</button>
        </SRow>
      </Section>
      <Section title="Hakkında">
        <SRow label="MediaMind 0.1"><span className="mono">{backendUrl()}</span></SRow>
      </Section>
    </>
  );
}

/* ---------------- Akıllı analiz ---------------- */

function Smart() {
  const { repoId, trackJob, jobs, toast } = useApp();
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["mlmodels"], queryFn: api.mlModels, refetchInterval: jobs.some((j) => j.type === "models" && j.status === "running") ? 1500 : false });
  const smart = useQuery({ queryKey: ["smart", repoId], queryFn: () => api.smart(repoId!), enabled: !!repoId });
  const downloading = jobs.some((j) => j.type === "models" && (j.status === "running" || j.status === "queued"));
  const missing = models.data?.models.filter((m) => !m.installed) ?? [];
  const s = smart.data;
  const toggle = async (key: "content" | "faces" | "video" | "transcripts" | "ocr" | "pets", v: boolean) => {
    await api.updateConfig(repoId!, { analysis: { [key]: v } });
    qc.invalidateQueries({ queryKey: ["smart", repoId] });
  };
  const pct = (a: number, b: number) => (b ? `%${Math.round((a / b) * 100)}` : "—");
  return (
    <>
      <Section title="Modeller" foot="Modeller bir kez indirilir ve tüm kütüphanelerde kullanılır. Analiz tamamen bu Mac'te yapılır; fotoğrafların hiçbir yere gönderilmez.">
        {models.data?.models.map((m) => (
          <SRow key={m.name} label={m.label} hint={`${m.purpose} · ${fmtBytes(m.size)}`}>
            {m.installed ? <span className="muted">Yüklü</span> : m.downloaded > 0 ? (
              <div className="bar" style={{ width: 120 }}><span style={{ width: `${Math.round((m.downloaded / m.size) * 100)}%` }} /></div>
            ) : <span className="muted">Yüklü değil</span>}
          </SRow>
        ))}
        {missing.length > 0 && (
          <SRow label="Eksik modelleri indir" hint={fmtBytes(missing.reduce((a, m) => a + m.size - m.downloaded, 0))}>
            <button className="btn pri" disabled={downloading} onClick={async () => { trackJob(await api.downloadModels()); toast("Modeller indiriliyor"); }}>
              {downloading ? "İndiriliyor…" : "İndir"}
            </button>
          </SRow>
        )}
      </Section>
      <ModelQuality Section={Section} SRow={SRow} />
      {s && repoId && (
        <>
          <Section title="Bu kütüphanede">
            <SRow label="İçeriği tanı" hint={`“Kumsalda köpek” gibi aramalar, otomatik etiketler ve seri çekimler · ${pct(s.content, s.total)} tamam`}>
              <Switch label="İçeriği tanı" checked={s.enabled.content} onChange={(v) => toggle("content", v)} />
            </SRow>
            <SRow label="Kişileri tanı" hint={`Yüzleri bulur ve aynı kişileri gruplar · ${fmtInt(s.faces_found)} yüz bulundu`}>
              <Switch label="Kişileri tanı" checked={s.enabled.faces} onChange={(v) => toggle("faces", v)} />
            </SRow>
            <SRow label="Videoları incele" hint={`Sahneler, videoda kimin ne zaman göründüğü ve video içinde arama · ${fmtInt(s.videos_done)} / ${fmtInt(s.videos)} video`}>
              <Switch label="Videoları incele" checked={s.enabled.video} onChange={(v) => toggle("video", v)} />
            </SRow>
            <SRow label="Konuşmaları yazıya dök" hint={s.transcript_model ? `Yavaştır: videonun yaklaşık dörtte biri kadar sürer · ${fmtInt(s.transcripts_done)} / ${fmtInt(s.videos)} video` : "Konuşma modeli gerekli"}>
              <Switch label="Konuşmaları yazıya dök" checked={s.enabled.transcripts} disabled={!s.transcript_model} onChange={(v) => toggle("transcripts", v)} />
            </SRow>
            <SRow label="Evcil hayvanları tanı" hint={s.pet_model ? `Kedi, köpek, kuş, at gibi hayvanları bulur; birine ad verince benzerleri de etiketlenir · ${fmtInt(s.pets_found)} hayvan bulundu` : "Evcil hayvan modeli gerekli (yukarıdan indir)"}>
              <Switch label="Evcil hayvanları tanı" checked={s.enabled.pets !== false} disabled={!s.pet_model} onChange={(v) => toggle("pets", v)} />
            </SRow>
            <SRow label="Fotoğraflardaki yazıyı oku" hint={s.ocr_model ? `“Fatura”, “pasaport”, tabela gibi yazılarla arama · ${fmtInt(s.ocr_done)} fotoğraf okundu, ${fmtInt(s.with_text)} tanesinde yazı var` : "macOS yazı tanıma kullanılamıyor"}>
              <Switch label="Fotoğraflardaki yazıyı oku" checked={s.enabled.ocr !== false} disabled={!s.ocr_model} onChange={(v) => toggle("ocr", v)} />
            </SRow>
          </Section>
          <Section foot="Yeni eklenen dosyalar kendiliğinden analiz edilir. Büyük arşivlerde ilk analiz uzun sürebilir.">
            <SRow label={s.content < s.total || s.faces < s.total || s.videos_done < s.videos ? "Analizi tamamla" : "Her şey analiz edildi"}
              hint={`İçerik ${fmtInt(s.content)} / ${fmtInt(s.total)} · yüz ${fmtInt(s.faces)} / ${fmtInt(s.total)}`}>
              <div className="row" style={{ gap: 6 }}>
                <button className="btn" disabled={!(s.content_model || s.face_model)} onClick={async () => trackJob(await api.smartRun(repoId, true))}>Baştan</button>
                <button className="btn pri" disabled={!(s.content_model || s.face_model)} onClick={async () => trackJob(await api.smartRun(repoId))}>Başlat</button>
              </div>
            </SRow>
          </Section>
        </>
      )}
    </>
  );
}

/* ---------------- Asistan ---------------- */

function AssistantTab() {
  const { toast } = useApp();
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["settings"], queryFn: api.settings }).data;
  const [key, setKey] = useState("");
  const provider = s?.settings.assistant.provider ?? "anthropic";
  const hasKey = !!s?.api_keys[provider];
  const models = useQuery({
    queryKey: ["models", provider, hasKey],
    queryFn: () => api.models(provider),
    enabled: !!s && provider !== "anthropic" && (hasKey || provider === "openrouter"),
    retry: false,
    staleTime: 600_000,
  });
  const [modelText, setModelText] = useState<string | null>(null);
  if (!s) return null;
  const set = async (p: Record<string, unknown>) => {
    try {
      qc.setQueryData(["settings"], await api.updateSettings(p));
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const saveKey = async () => {
    try {
      qc.setQueryData(["settings"], await api.setApiKey(key, provider));
      setKey("");
      toast("Anahtar Anahtar Zinciri'ne kaydedildi");
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const modelKey = provider === "anthropic" ? "model" : `${provider}_model`;
  const currentModel = provider === "anthropic" ? s.settings.assistant.model : s.settings.assistant[`${provider}_model` as "gemini_model"];
  const listId = `models-${provider}`;
  return (
    <>
      <Section title="Asistan" foot={<>Asistanı her yerden <b>⌘J</b> ile açabilirsin. Sorularını ve fotoğraf bilgilerini (dosya adı, tarih, yer) seçtiğin sağlayıcıya gönderir. Anahtarlar macOS Anahtar Zinciri'nde saklanır.</>}>
        <SRow label="Sağlayıcı">
          <select className="sel" value={provider} onChange={(e) => { setModelText(null); set({ assistant: { provider: e.target.value } }); }}>
            {Object.entries(s.providers).map(([id, p]) => <option key={id} value={id}>{p.label}</option>)}
          </select>
        </SRow>
        <SRow label="API anahtarı" hint={hasKey ? undefined : <>Anahtarı <span className="selectable">{s.providers[provider].key_url.replace("https://", "")}</span> adresinden alabilirsin.</>}>
          {hasKey ? (
            <div className="row" style={{ gap: 8 }}>
              <span className="mono">{s.api_keys[provider]}</span>
              <button className="btn sm" onClick={async () => qc.setQueryData(["settings"], await api.deleteApiKey(provider))}>Kaldır</button>
            </div>
          ) : (
            <div className="row" style={{ gap: 6 }}>
              <label className="field" style={{ width: 220 }}>
                <span className="sr">API anahtarı</span>
                <input type="password" placeholder={s.providers[provider].key_prefix ? `${s.providers[provider].key_prefix}…` : "Anahtar"} value={key}
                  onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => e.key === "Enter" && key && saveKey()} />
              </label>
              <button className="btn pri" disabled={!key} onClick={saveKey}>Kaydet</button>
            </div>
          )}
        </SRow>
        <SRow label="Model" hint={provider !== "anthropic" ? (models.isError ? (models.error as Error).message : !hasKey && provider === "gemini" ? "Model listesi için önce anahtarı ekle." : undefined) : undefined}>
          {provider === "anthropic" ? (
            <select className="sel" value={currentModel} onChange={(e) => set({ assistant: { model: e.target.value } })}>
              {Object.entries(s.models).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          ) : (
            <div className="field" style={{ width: 260 }}>
              <input list={listId} value={modelText ?? currentModel} placeholder={models.isLoading ? "Yükleniyor…" : "Model seç ya da yaz"}
                onChange={(e) => setModelText(e.target.value)} spellCheck={false}
                onBlur={() => modelText !== null && modelText !== currentModel && set({ assistant: { [modelKey]: modelText } })}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} style={{ fontFamily: "var(--mono)", fontSize: 12 }} />
              <datalist id={listId}>{models.data?.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</datalist>
            </div>
          )}
        </SRow>
        {provider === "anthropic" && (
          <SRow label="Yanıt tarzı">
            <div className="seg">
              {([["low", "Hızlı"], ["medium", "Dengeli"], ["high", "Derin"]] as const).map(([v, t]) => (
                <button key={v} className={cls(s.settings.assistant.effort === v && "on")} onClick={() => set({ assistant: { effort: v } })}>{t}</button>
              ))}
            </div>
          </SRow>
        )}
      </Section>
    </>
  );
}

/* ---------------- MCP ve izinler ---------------- */

function Mcp() {
  const { repoId, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const plans = useQuery({ queryKey: ["plans", repoId], queryFn: () => api.plans(repoId!), enabled: !!repoId });
  const [showTools, setShowTools] = useState(false);
  const s = q.data;
  const patch = async (p: Record<string, unknown>) => {
    try {
      qc.setQueryData(["settings"], await api.updateSettings(p));
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const copy = async (text: string) => {
    await navigator.clipboard.writeText(text);
    toast("Kopyalandı");
  };
  if (!s) return null;
  const a = s.settings.agents;
  const configText = JSON.stringify(s.mcp.config, null, 2);
  const agentPlans = (plans.data ?? []).filter((p: Plan) => p.source !== "ui" && p.source !== "cli").slice(0, 8);
  const SRC = (src: string) => (src === "assistant" ? "Asistan" : src.startsWith("mcp:") ? src.slice(4) : src);
  const ST: Record<string, string> = { pending: "Onay bekliyor", applied: "Uygulandı", undone: "Geri alındı", rejected: "Reddedildi", failed: "Başarısız", partial: "Kısmen", running: "Uygulanıyor" };
  return (
    <>
      <Section title="Asistan ve bağlı uygulamalar neler yapabilir" foot="Bu izinler hem yerleşik asistan hem de MCP ile bağlanan uygulamalar (Claude Desktop, Claude Code) için geçerlidir.">
        <SRow label="Fotoğrafları arama ve görme" hint="Kapalıyken hiçbir yapay zekâ arşivine erişemez.">
          <Switch label="Arama ve okuma" checked={a.read} onChange={(v) => patch({ agents: { read: v } })} />
        </SRow>
        <SRow label="Etiket, puan ve favori değiştirme" hint="Dosyalar taşınmaz ya da silinmez.">
          <Switch label="Etiket değiştirme" checked={a.tag} onChange={(v) => patch({ agents: { tag: v } })} />
        </SRow>
        <SRow label="Taşıma ve silmeden önce bana sor" hint="Kapatırsan yapay zekâ değişiklikleri kendisi uygulayabilir. Önerilmez.">
          <Switch label="Onay iste" checked={a.require_approval} onChange={(v) => patch({ agents: { require_approval: v } })} />
        </SRow>
        <SRow label="Önizleme görsellerini gönder" hint="Kapalıyken fotoğrafların bu Mac'ten çıkmaz; yalnızca bilgileri (tarih, yer, etiket) paylaşılır.">
          <Switch label="Önizleme gönder" checked={a.send_previews} onChange={(v) => patch({ agents: { send_previews: v } })} />
        </SRow>
      </Section>

      <Section title="Claude Desktop ve Claude Code ile bağlan" foot="Bağlantı MediaMind kapalıyken de çalışır. Önerilen değişiklikler senin onayını bekler.">
        <SRow label="Claude Desktop" hint="Ayarlar › Geliştirici › Yapılandırmayı düzenle yoluyla açılan dosyaya yapıştır.">
          <button className="btn" onClick={() => copy(configText)}>Yapılandırmayı kopyala</button>
        </SRow>
        <SRow label="Claude Code" hint={<span className="mono selectable">{s.mcp.claude_code}</span>}>
          <button className="btn" onClick={() => copy(s.mcp.claude_code)}>Komutu kopyala</button>
        </SRow>
        <SRow label={`${s.mcp.tools.length} araç`} hint="Bağlı uygulamaların kullanabildiği işlemler.">
          <button className="btn ghost" onClick={() => setShowTools((x) => !x)}>{showTools ? "Gizle" : "Göster"}</button>
        </SRow>
        {showTools && (
          <div className="grow-row" style={{ flexWrap: "wrap", gap: 4 }}>
            {s.mcp.tools.map((t) => <span key={t.name} className="tag mono" title={t.description}>{t.name}</span>)}
          </div>
        )}
      </Section>

      {repoId && agentPlans.length > 0 && (
        <Section title="Son önerileri">
          {agentPlans.map((p) => (
            <SRow key={p.id} label={p.title || p.kind} hint={`${SRC(p.source)} · ${fmtInt(p.summary.files)} dosya · ${fmtDate(p.created_at)}`}>
              <span className={cls("status", `st-${p.status}`)}>{ST[p.status] ?? p.status}</span>
            </SRow>
          ))}
        </Section>
      )}
    </>
  );
}

export function Settings({ initial = "general" }: { initial?: Tab }) {
  const { repoId } = useApp();
  const [tab, setTab] = useState<Tab>(initial);
  const TABS: [Tab, string][] = [["general", "Genel"], ["smart", "Akıllı analiz"], ["assistant", "Asistan"], ["mcp", "Gizlilik ve bağlantılar"]];
  return (
    <>
      <Toolbar title="Ayarlar">
        <div className="seg" role="tablist">
          {TABS.map(([t, l]) => <button key={t} role="tab" aria-selected={tab === t} className={cls(tab === t && "on")} onClick={() => setTab(t)}>{l}</button>)}
        </div>
        <div className="spacer" />
      </Toolbar>
      <div className="scroll">
        <div className="page" style={{ maxWidth: 680, gap: 22 }}>
          {tab === "general" && <General />}
          {tab === "smart" && (repoId ? <Smart /> : <div className="empty"><h2>Önce bir kütüphane ekle</h2></div>)}
          {tab === "assistant" && <AssistantTab />}
          {tab === "mcp" && <Mcp />}
        </div>
      </div>
    </>
  );
}
