import { useQuery } from "@tanstack/react-query";
import { api, thumbUrl } from "../api";
import { Sparkles } from "../components/Icons";
import { Toolbar } from "../components/Toolbar";
import { EMPTY_FILTER, useApp } from "../state";
import type { Memory } from "../types";
import { cls, fmtInt } from "../util";

function MemoryCard({ repoId, m, big }: { repoId: string; m: Memory; big?: boolean }) {
  const { setPage, setView, setFilter } = useApp();
  const open = () => {
    setView("all");
    setFilter({ ...EMPTY_FILTER, date_from: m.date_from, date_to: m.date_to, label: m.title });
    setPage("library");
  };
  const covers = m.cover.length >= 4 ? m.cover.slice(0, 4) : m.cover.slice(0, 1);
  return (
    <button className={cls("memory", big && "big")} onClick={open} title={`${m.title} · ${fmtInt(m.count)} öğe`}>
      <div className={cls("memory-img", covers.length === 4 && "quad")}>
        {covers.map((b) => <img key={b} src={thumbUrl(repoId, b, "m")} alt="" loading="lazy" draggable={false} />)}
      </div>
      <div className="memory-text">
        <b>{m.title}</b>
        <span>{m.subtitle}</span>
      </div>
    </button>
  );
}

export function Memories() {
  const { repoId } = useApp();
  const q = useQuery({ queryKey: ["memories", repoId], queryFn: () => api.memories(repoId!), enabled: !!repoId });
  if (!repoId) return null;
  const all = q.data?.memories ?? [];
  const today = all.filter((m) => m.kind === "on_this_day");
  const rest = all.filter((m) => m.kind !== "on_this_day");
  return (
    <>
      <Toolbar title="Anılar" subtitle={q.data ? `${fmtInt(all.length)} anı` : undefined} />
      <div className="scroll">
        <div className="page">
          {q.isLoading && <div className="loading-msg"><span className="spin" />Anılar hazırlanıyor…</div>}
          {q.data && all.length === 0 && (
            <div className="empty"><Sparkles size={40} /><h2>Henüz anı yok</h2>
              <span>Tarihli fotoğrafların çoğaldıkça geziler, kalabalık günler ve geçmiş yıllarda bugün burada toplanır.</span></div>
          )}
          {today.length > 0 && (
            <section className="col" style={{ gap: 10 }}>
              <h2>Geçmiş yıllarda bugün</h2>
              <div className="memory-grid">{today.map((m) => <MemoryCard key={m.key} repoId={repoId} m={m} big />)}</div>
            </section>
          )}
          {rest.length > 0 && (
            <section className="col" style={{ gap: 10 }}>
              <h2>Geziler ve günler</h2>
              <div className="memory-grid">{rest.map((m) => <MemoryCard key={m.key} repoId={repoId} m={m} />)}</div>
            </section>
          )}
          {all.length > 0 && <span className="muted" style={{ fontSize: 11.5 }}>Anılar fotoğrafların tarih ve yerlerinden kendiliğinden çıkarılır. En çok gün geçirdiğin yer “ev” sayılır; evden uzakta art arda günler gezi olur.</span>}
        </div>
      </div>
    </>
  );
}
