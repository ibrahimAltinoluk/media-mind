import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, thumbUrl } from "../api";
import { useTrashFlow } from "../components/DetailPanel";
import { CompressDialog } from "../components/EditDialogs";
import { Check, Play, Sparkles } from "../components/Icons";
import { Toolbar } from "../components/Toolbar";
import { useApp } from "../state";
import type { CleanupCategory } from "../types";
import { cls, fmtBytes, fmtDuration, fmtInt } from "../util";

/** Bir temizlik kategorisi: ızgara, hepsi seçili gelir; seçimi değiştir, sil ya da "bunlar kalsın". */
function Category({ repoId, c, onChanged }: { repoId: string; c: CleanupCategory; onChanged: () => void }) {
  const { openViewer, toast } = useApp();
  const trash = useTrashFlow();
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState(true);
  const [compress, setCompress] = useState<typeof c.items | null>(null);
  // Silme önerisi olan kategorilerde hepsi seçili başlar; büyük videolar yalnız gözden geçirme içindir
  useEffect(() => setSel(c.key === "large_videos" || c.key === "compressible" ? new Set() : new Set(c.items.map((i) => i.file_id))), [c]);
  const chosen = c.items.filter((i) => sel.has(i.file_id));
  const bytes = chosen.reduce((a, i) => a + i.size, 0);
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const keep = async () => {
    const rest = c.items.filter((i) => !sel.has(i.file_id));
    const ids = [...new Set((sel.size === c.items.length || sel.size === 0 ? c.items : rest).map((i) => i.asset_id))];
    await api.cleanupKeep(repoId, c.key, ids);
    toast(`${fmtInt(ids.length)} öğe bir daha önerilmeyecek`);
    onChanged();
  };
  return (
    <section className="clean-cat">
      <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
        <div className="grow" style={{ minWidth: 0 }}>
          <button className="clean-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <h2>{c.title}</h2>
            <span className="muted">{fmtInt(c.count)} öğe · {fmtBytes(c.bytes)}</span>
          </button>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{c.hint}</div>
        </div>
        <button className="btn" onClick={keep} title="Seçili olmayanlar (ya da hiçbiri seçili değilse hepsi) bir daha önerilmez">
          {sel.size && sel.size < c.items.length ? "Seçilmeyenler kalsın" : "Hepsi kalsın"}
        </button>
        {(c.key === "large_videos" || c.key === "compressible") && (
          <button className="btn pri" onClick={() => setCompress(sel.size ? chosen : c.items)} title="Videoları HEVC'ye çevirerek yer aç">
            {sel.size ? `${fmtInt(sel.size)} videoyu küçült…` : "Hepsini küçült…"}
          </button>
        )}
        <button className="btn danger" disabled={!sel.size} onClick={() => trash(chosen.map((i) => i.file_id), onChanged)}>
          {sel.size ? `${fmtInt(sel.size)} öğeyi sil (${fmtBytes(bytes)})…` : "Sil…"}
        </button>
      </div>
      {open && (
        <>
          <div className="row" style={{ gap: 8, margin: "8px 0 6px", fontSize: 12 }}>
            <button className="btn sm ghost" onClick={() => setSel(new Set(c.items.map((i) => i.file_id)))}>Hepsini seç</button>
            <button className="btn sm ghost" onClick={() => setSel(new Set())}>Seçimi kaldır</button>
            <span className="muted">Seçmek için tıkla, büyütmek için çift tıkla.</span>
          </div>
          <div className="trash-grid">
            {c.items.map((it, i) => (
              <button key={it.file_id} className={cls("tile", sel.has(it.file_id) && "sel")} onClick={() => toggle(it.file_id)}
                onDoubleClick={() => openViewer(c.items, i)} aria-pressed={sel.has(it.file_id)} title={`${it.rel_path}\n${fmtBytes(it.size)}`}>
                <img src={thumbUrl(repoId, it.blake3, "s")} alt="" loading="lazy" draggable={false} />
                {it.kind === "video" && <span className="badge b-vid"><Play size={10} />{fmtDuration(it.duration_ms)}</span>}
                {sel.has(it.file_id) && <span className="sel-check"><Check size={11} strokeWidth={3} /></span>}
              </button>
            ))}
          </div>
          {c.count > c.items.length && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>İlk {fmtInt(c.items.length)} öğe gösteriliyor.</div>}
        </>
      )}
      {compress && <CompressDialog repoId={repoId} fileIds={compress.map((i) => i.file_id)} bytes={compress.reduce((a, i) => a + i.size, 0)}
        onClose={() => setCompress(null)} onDone={onChanged} />}
    </section>
  );
}

export function Cleanup() {
  const { repoId } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cleanup", repoId], queryFn: () => api.cleanup(repoId!), enabled: !!repoId });
  if (!repoId) return null;
  const cats = q.data?.categories ?? [];
  const total = cats.filter((c) => c.key !== "large_videos" && c.key !== "compressible").reduce((a, c) => a + c.bytes, 0);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["cleanup", repoId] }); qc.invalidateQueries({ queryKey: ["summary", repoId] }); };
  return (
    <>
      <Toolbar title="Temizlik" subtitle={q.data ? (cats.length ? `Yaklaşık ${fmtBytes(total)} yer açılabilir` : "Önerilecek bir şey yok") : undefined} />
      <div className="scroll">
        <div className="page" style={{ maxWidth: 1080, gap: 26 }}>
          {q.isLoading && <div className="loading-msg"><span className="spin" />Öneriler hazırlanıyor…</div>}
          {q.data && cats.length === 0 && (
            <div className="empty"><Sparkles size={40} /><h2>Kütüphanen derli toplu</h2>
              <span>Ekran görüntüsü, bulanık ya da kazara çekilmiş fotoğraf bulunamadı. Tekrarlar için “Tekrarlar” bölümüne bak.</span></div>
          )}
          {cats.length > 0 && (
            <span className="muted" style={{ fontSize: 12 }}>
              Bunlar yalnızca öneri; hiçbir şey sormadan silinmez. Favoriler, puan verdiklerin ve albümlerdekiler önerilmez. Silinenler Son silinenler'e gider.
            </span>
          )}
          {cats.map((c) => <Category key={c.key} repoId={repoId} c={c} onChanged={refresh} />)}
        </div>
      </div>
    </>
  );
}
