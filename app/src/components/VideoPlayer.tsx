import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, frameUrl, playUrl, stripUrl, thumbUrl, waitJob } from "../api";
import { useApp } from "../state";
import type { AssetItem, Job } from "../types";
import { cls, fmtDuration } from "../util";
import { Camera, Close, Expand, Film, Pause, People, Play, Quote, Scissors, Search, Speaker, SpeakerOff } from "./Icons";

const COLORS = ["#ffd60a", "#64d2ff", "#30d158", "#ff9f0a", "#bf5af2", "#ff6482"];
const MIN_CLIP = 300;
const t = (ms: number) => fmtDuration(Math.max(ms, 0) + 1) || "0:00";

type Range = { a: number; b: number };
type Mark = { a: number; b: number };

/** Oynatma çizelgesi: tıkla/sürükle ile konum, aranan anlar işaretli; kırpma kipinde sarı çerçeve ve tutamaçlar. */
function Timeline({ dur, now, onSeek, marks, trim, setTrim, strip }: {
  dur: number; now: number; onSeek: (ms: number, final: boolean) => void; marks: Mark[];
  trim: Range | null; setTrim: (r: Range) => void; strip: string | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const drag = useRef<"seek" | "a" | "b" | null>(null);
  const toMs = (x: number) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.round(Math.max(0, Math.min(1, (x - r.left) / r.width)) * dur);
  };
  const pct = (ms: number) => `${(Math.max(0, Math.min(ms, dur)) / (dur || 1)) * 100}%`;

  const down = (e: React.PointerEvent, what: "seek" | "a" | "b") => {
    e.stopPropagation();
    e.preventDefault();
    drag.current = what;
    ref.current!.setPointerCapture(e.pointerId);
    move(e);
  };
  const move = (e: React.PointerEvent) => {
    const ms = toMs(e.clientX);
    setHover(ms);
    const d = drag.current;
    if (!d) return;
    if (d === "seek") onSeek(trim ? Math.max(trim.a, Math.min(trim.b, ms)) : ms, false);
    else if (trim && d === "a") { const a = Math.min(ms, trim.b - MIN_CLIP); setTrim({ ...trim, a: Math.max(0, a) }); onSeek(Math.max(0, a), false); }
    else if (trim && d === "b") { const b = Math.max(ms, trim.a + MIN_CLIP); setTrim({ ...trim, b: Math.min(dur, b) }); onSeek(Math.min(dur, b), false); }
  };
  const up = (e: React.PointerEvent) => {
    if (drag.current === "seek") onSeek(toMs(e.clientX), true);
    drag.current = null;
  };

  return (
    <div ref={ref} className={cls("vp-track", trim && "trim")} role="slider" aria-label="Konum" aria-valuemin={0} aria-valuemax={dur} aria-valuenow={now}
      aria-valuetext={t(now)} tabIndex={-1}
      onPointerDown={(e) => down(e, "seek")} onPointerMove={move} onPointerUp={up} onPointerLeave={() => !drag.current && setHover(null)}
      style={trim && strip ? { backgroundImage: `url("${strip}")` } : undefined}>
      {!trim && <span className="fill" style={{ width: pct(now) }} />}
      {!trim && marks.map((m, i) => <span key={i} className="mark" style={{ left: pct(m.a), width: `max(3px, calc(${pct(m.b)} - ${pct(m.a)}))` }} />)}
      {trim && (
        <>
          <span className="shade" style={{ left: 0, width: pct(trim.a) }} />
          <span className="shade" style={{ left: pct(trim.b), right: 0 }} />
          <span className="frame" style={{ left: pct(trim.a), width: `calc(${pct(trim.b)} - ${pct(trim.a)})` }}>
            <span className="handle l" onPointerDown={(e) => down(e, "a")} aria-label="Başlangıç" role="slider" aria-valuenow={trim.a} />
            <span className="handle r" onPointerDown={(e) => down(e, "b")} aria-label="Bitiş" role="slider" aria-valuenow={trim.b} />
          </span>
        </>
      )}
      <span className="head" style={{ left: pct(now) }} />
      {hover !== null && <span className="tip" style={{ left: pct(hover) }}>{t(hover)}</span>}
    </div>
  );
}

type Panel = "scenes" | "people" | "transcript" | null;

/** Görüntüleyicideki video: kendi kontrolleri, sahneler, kişiler, konuşma, videoda arama, kare ve kırpma. */
export function VideoPlayer({ repoId, item }: { repoId: string; item: AssetItem }) {
  const { toast, trackJob } = useApp();
  const qc = useQueryClient();
  const ref = useRef<HTMLVideoElement>(null);
  const info = useQuery({ queryKey: ["video", repoId, item.asset_id], queryFn: () => api.videoInfo(repoId, item.asset_id) });
  const tr = useQuery({ queryKey: ["transcript", repoId, item.asset_id], queryFn: () => api.transcript(repoId, item.asset_id) });
  const [proxyJob, setProxyJob] = useState<Job | null>(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(() => { try { return localStorage.getItem("mm.muted") === "1"; } catch { return false; } });
  const [panel, setPanel] = useState<Panel>(() => { try { return (localStorage.getItem("mm.vpanel") as Panel) || null; } catch { return null; } });
  const [trim, setTrim] = useState<Range | null>(null);
  const [q, setQ] = useState("");
  const [trq, setTrq] = useState("");
  const [moments, setMoments] = useState<{ ms: number; end_ms: number; score: number }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [dur, setDur] = useState(item.duration_ms ?? 0);
  const canPlay = info.data ? info.data.playable || info.data.proxy_ready : true;
  const d = info.data;

  useEffect(() => {
    setFailed(false);
    setProxyJob(null);
    setTrim(null);
    setMoments(null);
    setQ("");
    setNow(0);
    setDur(item.duration_ms ?? 0);
  }, [item.blake3, item.duration_ms]);
  useEffect(() => { try { localStorage.setItem("mm.vpanel", panel ?? ""); } catch { /* yok */ } }, [panel]);
  useEffect(() => { try { localStorage.setItem("mm.muted", muted ? "1" : "0"); } catch { /* yok */ } }, [muted]);

  // Oynatılamıyorsa önizleme kopyasını kendiliğinden hazırla
  useEffect(() => {
    if (!info.data || canPlay || proxyJob) return;
    let alive = true;
    api.makeProxy(repoId, item.blake3).then(async (j) => {
      if (!alive) return;
      setProxyJob(j);
      try {
        await waitJob(j.id, (x) => alive && setProxyJob(x));
        await qc.invalidateQueries({ queryKey: ["video", repoId, item.asset_id] });
      } catch (e) {
        alive && toast(String((e as Error).message), { error: true });
      }
    });
    return () => { alive = false; };
  }, [info.data, canPlay, proxyJob, repoId, item.blake3, item.asset_id, qc, toast]);

  const seek = useCallback((ms: number, play = false) => {
    const v = ref.current;
    if (!v) return;
    v.currentTime = ms / 1000;
    setNow(ms);
    if (play) v.play().catch(() => undefined);
  }, []);
  const toggle = useCallback(() => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) {
      if (trim && (v.currentTime * 1000 < trim.a || v.currentTime * 1000 >= trim.b - 50)) v.currentTime = trim.a / 1000;
      v.play().catch(() => undefined);
    } else v.pause();
  }, [trim]);
  const fullscreen = () => {
    const v = ref.current as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
    if (!v) return;
    if (v.requestFullscreen) v.requestFullscreen().catch(() => v.webkitEnterFullscreen?.());
    else v.webkitEnterFullscreen?.();
  };

  const run = async (fn: () => Promise<Job>, okText: (rel: string) => string) => {
    setBusy(true);
    let j: Job;
    try {
      j = await fn();
    } catch (e) {
      toast(String((e as Error).message), { error: true });
      setBusy(false);
      return false;
    }
    trackJob(j);
    let ok = false;
    try {
      const done = await waitJob(j.id);
      toast(okText(done.result.rel_path));
      qc.invalidateQueries({ queryKey: ["assets", repoId] });
      ok = true;
    } catch {
      /* iş hatası iş izleyicisi tarafından zaten bildirildi */
    }
    setBusy(false);
    return ok;
  };
  const saveClip = async () => {
    if (!trim) return;
    ref.current?.pause();
    if (await run(() => api.clip(repoId, item.asset_id, trim.a, trim.b), (r) => `Klip kaydedildi: ${r.split("/").pop()}`)) setTrim(null);
  };
  const startTrim = () => {
    ref.current?.pause();
    const a = Math.min(now, Math.max(0, dur - MIN_CLIP));
    setTrim({ a: dur > 20000 ? a : 0, b: dur > 20000 ? Math.min(dur, a + 10000) : dur });
  };
  const search = async () => {
    if (!q.trim()) return setMoments(null);
    const r = (await api.moments(repoId, item.asset_id, q.trim())).moments;
    setMoments(r);
    if (r.length) seek(r[0].ms, true);
  };

  // Klavye: boşluk oynat/duraklat, M ses; kırpmada Enter kaydet, Esc vazgeç (görüntüleyiciyi kapatmadan)
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select");
      if (trim && e.key === "Escape") { setTrim(null); e.preventDefault(); e.stopImmediatePropagation(); return; }
      if (typing || e.metaKey) return;
      if (e.key === " ") { toggle(); e.preventDefault(); e.stopImmediatePropagation(); }
      else if (e.key === "m" || e.key === "M") setMuted((m) => !m);
      else if (trim && e.key === "Enter") { saveClip(); e.preventDefault(); }
    };
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  });

  const pct = proxyJob && proxyJob.total ? Math.round((proxyJob.done / proxyJob.total) * 100) : 0;
  const ready = canPlay && !failed;
  const people = d?.people ?? [];
  const scenes = d?.scenes ?? [];
  const segs = tr.data?.segments ?? [];
  const marks: Mark[] = (moments ?? []).map((m) => ({ a: m.ms, b: Math.min(m.end_ms, dur || m.end_ms) }));
  const panelBtn = (p: Exclude<Panel, null>, icon: React.ReactNode, label: string, n: number) => n > 0 && (
    <button className={cls("vp-chip", panel === p && "on")} onClick={() => setPanel(panel === p ? null : p)} aria-pressed={panel === p}>
      {icon}{label}<span className="n">{n}</span>
    </button>
  );

  return (
    <div className="vp">
      <div className="vp-stage">
        {!ready ? (
          <div className="msg">
            {failed ? (
              <><p style={{ fontWeight: 600, margin: "0 0 6px" }}>Bu video oynatılamadı</p><p style={{ margin: 0, color: "#a1a1a6" }}>Dosya bozuk olabilir.</p></>
            ) : (
              <>
                <p style={{ fontWeight: 600, margin: "0 0 8px" }}>Video hazırlanıyor…</p>
                <p style={{ margin: "0 0 12px", color: "#a1a1a6" }}>Bu biçim doğrudan oynatılamıyor, izlenebilir bir kopyası hazırlanıyor. Orijinal dosyaya dokunulmuyor.</p>
                <div className="bar" style={{ width: 260, margin: "0 auto" }}><span style={{ width: `${pct}%` }} /></div>
              </>
            )}
          </div>
        ) : (
          <video key={`${item.blake3}-${d?.proxy_ready}`} ref={ref} src={playUrl(repoId, item.blake3)} poster={thumbUrl(repoId, item.blake3, "m")}
            autoPlay playsInline muted={muted} onError={() => setFailed(true)} onClick={toggle} onDoubleClick={fullscreen}
            onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
            onLoadedMetadata={(e) => { const ms = Math.round(e.currentTarget.duration * 1000); if (Number.isFinite(ms) && ms > 0) setDur(ms); }}
            onTimeUpdate={(e) => {
              const ms = Math.round(e.currentTarget.currentTime * 1000);
              if (trim && ms >= trim.b && !e.currentTarget.paused) { e.currentTarget.pause(); e.currentTarget.currentTime = trim.b / 1000; setNow(trim.b); return; }
              setNow(ms);
            }} />
        )}
      </div>

      {ready && (
        <div className="vp-dock">
          <div className="vp-bar">
            <button className="vp-btn" onClick={toggle} aria-label={playing ? "Duraklat (boşluk)" : "Oynat (boşluk)"} title={playing ? "Duraklat (boşluk)" : "Oynat (boşluk)"}>
              {playing ? <Pause size={16} /> : <Play size={16} />}
            </button>
            <span className="vp-time">{t(now)}</span>
            <Timeline dur={dur} now={now} onSeek={(ms) => seek(ms)} marks={marks} trim={trim} setTrim={setTrim}
              strip={trim ? stripUrl(repoId, item.blake3, 12) : null} />
            <span className="vp-time">−{t(Math.max(0, dur - now))}</span>
            <button className="vp-btn" onClick={() => setMuted((m) => !m)} aria-label={muted ? "Sesi aç (M)" : "Sesi kapat (M)"} title={muted ? "Sesi aç (M)" : "Sesi kapat (M)"}>
              {muted ? <SpeakerOff size={16} /> : <Speaker size={16} />}
            </button>
            <button className="vp-btn" onClick={fullscreen} aria-label="Tam ekran" title="Tam ekran (çift tıkla)"><Expand size={15} /></button>
          </div>

          {trim ? (
            <div className="vp-tools trim">
              <span className="grow" style={{ color: "#a1a1a6" }}>
                Sarı çerçevenin kenarlarını sürükleyerek kesilecek bölümü seç. <b style={{ color: "#ffd60a" }}>{t(trim.a)} – {t(trim.b)}</b> ({t(trim.b - trim.a)})
              </span>
              <button className="vp-chip" onClick={() => setTrim(null)}>Vazgeç</button>
              <button className="vp-chip pri" disabled={busy || trim.b - trim.a < MIN_CLIP} onClick={saveClip}>{busy ? "Kaydediliyor…" : "Klibi kaydet"}</button>
            </div>
          ) : (
            <div className="vp-tools">
              {d?.analyzed && (
                <label className="vp-search">
                  <Search size={13} />
                  <span className="sr">Videoda ara</span>
                  <input value={q} onChange={(e) => { setQ(e.target.value); if (!e.target.value) setMoments(null); }} placeholder="Videoda ara: “pasta kesilen an”"
                    onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") search(); if (e.key === "Escape") e.currentTarget.blur(); }} />
                  {moments && <button className="x" onClick={() => { setQ(""); setMoments(null); }} aria-label="Aramayı temizle"><Close size={11} /></button>}
                </label>
              )}
              {panelBtn("scenes", <Film size={14} />, "Sahneler", scenes.length)}
              {panelBtn("people", <People size={14} />, "Kişiler", people.length)}
              {panelBtn("transcript", <Quote size={14} />, "Konuşma", segs.length)}
              <span className="grow" />
              <button className="vp-chip" disabled={busy} onClick={() => run(() => api.snapshot(repoId, item.asset_id, now), (r) => `Kare fotoğraf olarak kaydedildi: ${r.split("/").pop()}`)}
                title="Şu anki kareyi fotoğraf olarak kaydet"><Camera size={14} />Kareyi kaydet</button>
              <button className="vp-chip" onClick={startTrim} title="Videonun bir bölümünü ayrı klip olarak kaydet"><Scissors size={14} />Kırp</button>
            </div>
          )}

          {!trim && moments && (
            <div className="vp-moments">
              {moments.length ? moments.map((m) => (
                <button key={m.ms} className="vp-chip" onClick={() => seek(m.ms, true)}>{t(m.ms)} – {t(Math.min(m.end_ms, dur || m.end_ms))}</button>
              )) : <span style={{ color: "#a1a1a6" }}>“{q}” bu videoda bulunamadı.</span>}
            </div>
          )}

          {!trim && d && !d.analyzed && (
            <div className="vp-note">Sahneler, kişiler ve videoda arama, video incelendikten sonra görünür (Ayarlar › Akıllı analiz).</div>
          )}

          {!trim && panel === "scenes" && scenes.length > 0 && (
            <div className="vp-panel vp-scenes">
              {scenes.map((s) => {
                const on = now >= s.start_ms && now < s.end_ms;
                return (
                  <button key={s.idx} className={cls(on && "on")} onClick={() => seek(s.start_ms, true)} title={`${t(s.start_ms)} – ${t(s.end_ms)}`}>
                    <img src={frameUrl(repoId, item.blake3, s.key_ms)} alt="" loading="lazy" />
                    <span>{t(s.start_ms)}</span>
                  </button>
                );
              })}
            </div>
          )}

          {!trim && panel === "people" && people.length > 0 && dur > 0 && (
            <div className="vp-panel vp-people">
              {people.map((p, i) => (
                <div key={p.person_id} className="row-p">
                  <span className="ellipsis">{p.name}</span>
                  <div className="lane">
                    {p.segments.map(([a, b], k) => (
                      <button key={k} onClick={() => seek(a, true)} title={`${p.name}: ${t(a)} – ${t(b)}`} aria-label={`${p.name} ${t(a)}`}
                        style={{ left: `${(a / dur) * 100}%`, width: `${Math.max(0.6, ((Math.min(b, dur) - a) / dur) * 100)}%`, background: COLORS[i % COLORS.length] }} />
                    ))}
                    <span className="now" style={{ left: `${(now / dur) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!trim && panel === "transcript" && segs.length > 0 && (
            <div className="vp-panel">
              <label className="vp-search" style={{ marginBottom: 6, maxWidth: 260 }}>
                <Search size={13} />
                <span className="sr">Konuşmada ara</span>
                <input value={trq} onChange={(e) => setTrq(e.target.value)} placeholder="Konuşmada ara"
                  onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Escape") e.currentTarget.blur(); }} />
              </label>
              <div className="vp-transcript selectable">
                {segs.filter((sg) => !trq.trim() || sg.text.toLocaleLowerCase("tr").includes(trq.trim().toLocaleLowerCase("tr"))).map((sg) => {
                  const on = now >= sg.start_ms && now < sg.end_ms;
                  return (
                    <button key={sg.start_ms} className={cls(on && "on")} onClick={() => seek(sg.start_ms, true)}>
                      <span className="ts">{t(sg.start_ms)}</span><span>{sg.text}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
