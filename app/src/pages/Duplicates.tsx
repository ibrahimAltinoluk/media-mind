import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, thumbUrl } from "../api";
import { Check, Sliders, Stack } from "../components/Icons";
import { Popover } from "../components/Popover";
import { Toolbar } from "../components/Toolbar";
import { useApp, useSummary } from "../state";
import type { AssetItem, DupGroup, DupMember } from "../types";
import { basename, cls, fmtBytes, fmtInt } from "../util";

type Filter = "all" | "copies" | "similar" | "burst";
const TITLE: Record<DupGroup["kind"], string> = { exact: "Birebir aynı", near: "Neredeyse aynı", video: "Aynı video", burst: "Seri çekim" };
const match = (f: Filter, k: DupGroup["kind"]) => f === "all" || (f === "copies" && (k === "exact" || k === "video")) || (f === "similar" && k === "near") || (f === "burst" && k === "burst");

function toItem(m: DupMember): AssetItem {
  return {
    file_id: m.file_id, rel_path: m.rel_path, tracked: m.tracked ? 1 : 0, size: m.size, asset_id: m.asset_id, blake3: m.blake3,
    kind: m.kind as "photo" | "video", mime: null, width: m.width, height: m.height, duration_ms: null, taken_at: m.taken_at,
    taken_at_source: null, camera_model: null, rating: 0, favorite: 0, lat: null, lon: null, place: null, region: null, country_code: null, copies: 1, in_dup: 1,
  };
}

function Options({ anchor, onClose, rule, setRule, threshold, setThreshold, primary }: {
  anchor: HTMLElement | null; onClose: () => void; rule: string; setRule: (r: string) => void;
  threshold: number; setThreshold: (n: number) => void; primary: string[];
}) {
  return (
    <Popover anchor={anchor} onClose={onClose} width={320}>
      <div style={{ padding: "6px 8px 8px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="lbl" style={{ padding: "2px 2px 0" }}>Hangi kopya kalsın?</div>
        {([
          ["quality", "En iyi kalite", "En yüksek çözünürlük ve en eksiksiz bilgi"],
          ["oldest", "En eski", "İlk çekilen ya da ilk kaydedilen"],
          ["primary_folder", "Ana klasördeki", primary.length ? primary.join(", ") : "Ayarlar › Genel'den seçilir"],
        ] as const).map(([v, t, h]) => (
          <label key={v} className="check" style={{ alignItems: "flex-start" }}>
            <input type="radio" name="rule" checked={rule === v} onChange={() => setRule(v)} style={{ marginTop: 2 }} />
            <span><span style={{ display: "block" }}>{t}</span><span className="muted" style={{ fontSize: 11.5 }}>{h}</span></span>
          </label>
        ))}
        <div className="msep" style={{ margin: "2px 0" }} />
        <div className="lbl" style={{ padding: "0 2px" }}>Benzerlik</div>
        <div className="row" style={{ gap: 8, fontSize: 11.5 }}>
          <span className="muted">Katı</span>
          <input type="range" className="grow" min={0} max={14} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} style={{ accentColor: "var(--accent)" }} aria-label="Benzerlik toleransı" />
          <span className="muted">Gevşek</span>
        </div>
        <span className="muted" style={{ fontSize: 11.5 }}>Gevşek ayar yeniden boyutlanmış ya da sıkıştırılmış kopyaları da bulur.</span>
      </div>
    </Popover>
  );
}

function GroupRow({ g, repoId, del, onToggle, onMerge, onOpen }: {
  g: DupGroup; repoId: string; del: Set<number>; onToggle: (m: DupMember) => void; onMerge: () => void; onOpen: (i: number) => void;
}) {
  const kept = g.members.filter((m) => !del.has(m.file_id));
  const removed = g.members.filter((m) => del.has(m.file_id));
  const bytes = removed.reduce((a, m) => a + m.size, 0);
  const burst = g.kind === "burst";
  return (
    <div className="dup-row">
      <div className="dup-thumbs">
        {g.members.map((m, i) => {
          const keep = !del.has(m.file_id);
          return (
            <button key={m.file_id} className={cls(!keep && "drop")} onClick={() => onToggle(m)} onDoubleClick={() => onOpen(i)}
              title={`${m.rel_path}\n${m.width ? `${m.width}×${m.height} · ` : ""}${fmtBytes(m.size)}\n${m.reason}\n\nTıkla: ${keep ? "silinecekler arasına al" : "tut"} · Çift tıkla: büyüt`}
              aria-pressed={keep} aria-label={`${basename(m.rel_path)}: ${keep ? "kalacak" : "silinecek"}`}>
              <img src={thumbUrl(repoId, m.blake3, "s")} alt="" loading="lazy" draggable={false} />
              {keep && <span className="keep-mark"><Check size={10} strokeWidth={3} /></span>}
            </button>
          );
        })}
      </div>
      <div className="grow" style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{TITLE[g.kind]} <span className="muted" style={{ fontWeight: 400 }}>· {g.members.length} {g.members[0]?.kind === "video" ? "video" : "fotoğraf"}</span></div>
        <div className="muted ellipsis" style={{ fontSize: 12, marginTop: 2 }}>
          {removed.length
            ? <>Kalacak: {kept.map((m) => basename(m.rel_path)).join(", ")} · {fmtBytes(bytes)} yer açılır</>
            : burst ? "Benzer kareler. Kalmasını istediklerini seç ya da en iyisini tut." : "Hiçbiri silinmeyecek"}
        </div>
      </div>
      {burst && !removed.length
        ? <button className="btn" onClick={onMerge}>En iyisini tut</button>
        : <button className="btn" disabled={!removed.length} onClick={onMerge}>Birleştir</button>}
    </div>
  );
}

export function Duplicates() {
  const { repoId, showPlan, toast, openViewer } = useApp();
  const { data: s } = useSummary(repoId);
  const [rule, setRuleRaw] = useState<string>("");
  const [threshold, setThreshold] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [optsOpen, setOptsOpen] = useState(false);
  const optsBtn = useRef<HTMLButtonElement>(null);
  const q = useQuery({
    queryKey: ["dups", repoId, rule, threshold],
    queryFn: () => api.dups(repoId!, { rule: rule || undefined, threshold: threshold ?? undefined }),
    enabled: !!repoId && rule !== "",
  });
  const [del, setDel] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (q.data) setDel(new Set(q.data.groups.flatMap((g) => g.members.filter((m) => m.role === "delete").map((m) => m.file_id))));
  }, [q.data]);
  useEffect(() => {
    if (s && rule === "") setRuleRaw(s.config.dedup.keep_rule);
    if (s && threshold === null) setThreshold(s.config.dedup.near_threshold);
  }, [s, rule, threshold]);

  const groups = useMemo(() => (q.data?.groups ?? []).filter((g) => match(filter, g.kind)), [q.data, filter]);
  const selected = useMemo(() => {
    const ms = groups.flatMap((g) => g.members).filter((m) => del.has(m.file_id));
    return { files: ms.map((m) => m.file_id), bytes: ms.reduce((a, m) => a + m.size, 0) };
  }, [del, groups]);

  const toggle = (g: DupGroup, m: DupMember) => {
    const next = new Set(del);
    if (next.has(m.file_id)) next.delete(m.file_id);
    else {
      if (!g.members.some((x) => x.file_id !== m.file_id && !next.has(x.file_id))) {
        toast("Her gruptan en az bir dosya kalmalı");
        return;
      }
      next.add(m.file_id);
    }
    setDel(next);
  };

  const plan = async (ids: number[], title: string) => {
    if (!ids.length) return;
    try {
      showPlan(await api.createPlan(repoId!, { kind: "dedup", file_ids: ids, title }), () => q.refetch());
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };
  const mergeGroup = (g: DupGroup) => {
    let ids = g.members.filter((m) => del.has(m.file_id)).map((m) => m.file_id);
    if (!ids.length && g.kind === "burst") {
      // En iyi kare (kural sırasına göre ilk "keep") kalır
      const best = g.members.find((m) => m.role === "keep") ?? g.members[0];
      ids = g.members.filter((m) => m.file_id !== best.file_id).map((m) => m.file_id);
    }
    plan(ids, g.kind === "burst" ? "Seri çekimden en iyisini tut" : "Kopyaları birleştir");
  };

  const setRule = async (r: string) => {
    setRuleRaw(r);
    await api.updateConfig(repoId!, { dedup: { keep_rule: r } });
  };

  const all = q.data?.groups ?? [];
  const count = (f: Filter) => all.filter((g) => match(f, g.kind)).length;
  const gain = all.filter((g) => g.kind !== "burst").reduce((a, g) => a + g.gain_bytes, 0);
  return (
    <>
      <Toolbar title="Tekrarlar" subtitle={q.data ? (all.length ? `${fmtInt(all.length)} grup · ${fmtBytes(gain)} yer açılabilir` : "Tekrar yok") : undefined}>
        <div className="seg">
          {([["all", "Tümü"], ["copies", "Kopyalar"], ["similar", "Benzerler"], ["burst", "Seri çekimler"]] as const).map(([f, t]) => (
            <button key={f} className={cls(filter === f && "on")} onClick={() => setFilter(f)}>{t}{q.data && f !== "all" ? ` ${count(f)}` : ""}</button>
          ))}
        </div>
        <button ref={optsBtn} className={cls("ibtn", optsOpen && "on")} onClick={() => setOptsOpen((o) => !o)} title="Seçenekler" aria-label="Seçenekler"><Sliders size={17} /></button>
        <button className="btn pri" disabled={!selected.files.length} onClick={() => plan(selected.files, "Tekrarları birleştir")}>
          {selected.files.length ? `Tümünü birleştir (${fmtInt(selected.files.length)})` : "Tümünü birleştir"}
        </button>
      </Toolbar>
      {optsOpen && <Options anchor={optsBtn.current} onClose={() => setOptsOpen(false)} rule={rule} setRule={setRule}
        threshold={threshold ?? 6} setThreshold={setThreshold} primary={s?.config.dedup.primary_folders ?? []} />}
      <div className="scroll">
        <div className="page" style={{ maxWidth: 940, gap: 0 }}>
          {groups.length > 0 && (
            <div className="muted" style={{ fontSize: 12, paddingBottom: 6 }}>
              İşaretli olanlar kalır. Değiştirmek için fotoğrafa tıkla, büyütmek için çift tıkla. Silinenler {fmtBytes(selected.bytes)} yer açar ve
              Son silinenler'e gider; istediğinde geri alabilirsin.
            </div>
          )}
          {q.isLoading && <div className="muted" style={{ padding: 24 }}>Tekrarlar aranıyor…</div>}
          {q.data && groups.length === 0 && (
            <div className="empty"><Stack size={40} /><h2>Tekrar yok</h2><span>{filter === "all" ? "Kütüphanende aynı ya da neredeyse aynı fotoğraf bulunamadı." : "Bu türde tekrar bulunamadı."}</span></div>
          )}
          {groups.slice(0, 300).map((g) => (
            <GroupRow key={g.id} g={g} repoId={repoId!} del={del} onToggle={(m) => toggle(g, m)} onMerge={() => mergeGroup(g)}
              onOpen={(i) => openViewer(g.members.map(toItem), i)} />
          ))}
          {groups.length > 300 && <div className="muted" style={{ padding: 12 }}>İlk 300 grup gösteriliyor. “Tümünü birleştir” hepsine uygulanır.</div>}
        </div>
      </div>
    </>
  );
}
