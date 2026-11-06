import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Arrow, Chevron } from "../components/Icons";
import { Toolbar } from "../components/Toolbar";
import { useApp } from "../state";
import { cls, fmtInt } from "../util";

const PRESETS: { key: string; title: string; tpl: string }[] = [
  { key: "yil-ay", title: "Yıl ve ay", tpl: "{yıl}/{ay}-{ay_adı}/{dosya}" },
  { key: "yil", title: "Yıl", tpl: "{yıl}/{dosya}" },
  { key: "yil-konum", title: "Yıl ve şehir", tpl: "{yıl}/{şehir}/{dosya}" },
  { key: "kisi", title: "Kişi", tpl: "{kişi}/{yıl}/{dosya}" },
  { key: "tur-yil-ay", title: "Fotoğraf / video ayrı", tpl: "{tür}/{yıl}/{ay}-{ay_adı}/{dosya}" },
  { key: "kamera-yil", title: "Kamera", tpl: "{kamera}/{yıl}/{dosya}" },
];
const TOKENS: [string, string][] = [
  ["{yıl}", "Yıl"], ["{ay}", "Ay (03)"], ["{ay_adı}", "Ay adı"], ["{gün}", "Gün"], ["{şehir}", "Şehir"], ["{il}", "İl"], ["{ülke}", "Ülke"],
  ["{kişi}", "Kişi"], ["{etiket}", "Etiket"], ["{kamera}", "Kamera"], ["{tür}", "Tür"], ["{dosya}", "Dosya adı"], ["{ad}", "Ad (uzantısız)"], ["{uzantı}", "Uzantı"],
];
const SAMPLE: Record<string, string> = {
  "{yıl}": "2024", "{ay}": "07", "{ay_adı}": "Temmuz", "{gün}": "14", "{şehir}": "Bodrum", "{il}": "Muğla", "{ülke}": "TR",
  "{kişi}": "Ayşe", "{etiket}": "tatil", "{kamera}": "iPhone 15 Pro", "{tür}": "Fotoğraflar", "{dosya}": "IMG_0421.HEIC", "{ad}": "IMG_0421", "{uzantı}": "HEIC",
};
const example = (tpl: string) => tpl.replace(/\{[^{}]+\}/g, (t) => SAMPLE[t] ?? t);

export function Organize() {
  const { repoId, showPlan, toast } = useApp();
  const [template, setTemplate] = useState(PRESETS[0].tpl);
  const [custom, setCustom] = useState(false);
  const [debounced, setDebounced] = useState(template);
  const [scope, setScope] = useState("tracked");
  const [base, setBase] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(template), 300);
    return () => clearTimeout(t);
  }, [template]);

  const q = useQuery({
    queryKey: ["organize", repoId, debounced, scope, base],
    queryFn: () => api.organizePreview(repoId!, { template: debounced, scope, base }),
    enabled: !!repoId && !!debounced.trim(),
    retry: false,
  });

  const insert = (tok: string) => {
    const el = input.current;
    if (!el) return setTemplate((t) => t + tok);
    const s = el.selectionStart ?? template.length;
    const e = el.selectionEnd ?? template.length;
    setTemplate(template.slice(0, s) + tok + template.slice(e));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(s + tok.length, s + tok.length);
    });
  };

  const create = async () => {
    try {
      const plan = await api.createPlan(repoId!, { kind: "organize", template, scope, base });
      if (!plan.item_count) {
        toast("Her şey zaten yerinde.");
        return;
      }
      showPlan(plan, () => q.refetch());
    } catch (e) {
      toast(String((e as Error).message), { error: true });
    }
  };

  const total = q.data?.total ?? 0;
  return (
    <>
      <Toolbar title="Klasörleri düzenle" subtitle="Dosyalarını seçtiğin düzene göre klasörlere taşır. Geri alınabilir.">
        <button className="btn pri" onClick={create} disabled={!total}>{total ? `${fmtInt(total)} dosyayı düzenle…` : "Düzenle…"}</button>
      </Toolbar>
      <div className="scroll">
        <div className="page" style={{ maxWidth: 980 }}>
          <section className="col" style={{ gap: 10 }}>
            <h2>Nasıl düzenlensin?</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 10 }}>
              {PRESETS.map((p) => (
                <button key={p.key} className={cls("preset", !custom && template === p.tpl && "on")} onClick={() => { setCustom(false); setTemplate(p.tpl); }}>
                  <b>{p.title}</b>
                  <span className="path ellipsis" title={example(p.tpl)}>{example(p.tpl)}</span>
                </button>
              ))}
              <button className={cls("preset", custom && "on")} onClick={() => { setCustom(true); requestAnimationFrame(() => input.current?.focus()); }}>
                <b>Özel</b>
                <span className="path">Kendi düzenini oluştur</span>
              </button>
            </div>
          </section>

          {custom && (
            <section className="card">
              <div className="field" style={{ height: 34, boxShadow: q.isError ? "0 0 0 2px var(--danger)" : undefined }}>
                <input ref={input} value={template} onChange={(e) => setTemplate(e.target.value)} spellCheck={false} aria-label="Klasör şablonu"
                  style={{ fontFamily: "var(--mono)", fontSize: 13 }} />
              </div>
              {q.isError ? <span style={{ color: "var(--danger)", fontSize: 12 }}>{(q.error as Error).message}</span>
                : <span className="muted" style={{ fontSize: 12 }}>Örnek: <span className="mono">{example(template)}</span></span>}
              <div className="row wrap" style={{ gap: 4 }}>
                {TOKENS.map(([t, l]) => <button key={t} className="tok" onClick={() => insert(t)} title={t}>{l}</button>)}
              </div>
            </section>
          )}

          <section className="col" style={{ gap: 10 }}>
            <div className="row">
              <h2 className="grow">Önizleme</h2>
              <span className="muted" style={{ fontSize: 12 }}>{q.data ? `${fmtInt(total)} dosya taşınacak · ${fmtInt(q.data.unchanged)} zaten yerinde` : "Hesaplanıyor…"}</span>
            </div>
            <div className="row wrap" style={{ alignItems: "stretch", gap: 12 }}>
              <div className="group" style={{ flex: "2 1 480px", maxHeight: 420, overflowY: "auto" }}>
                {q.data?.moves.slice(0, 120).map((m) => (
                  <div key={m.file_id} className="mv">
                    <span className="muted" title={m.src}>{m.src}</span>
                    <Arrow size={13} style={{ color: "var(--muted)" }} />
                    <span title={m.dst}>{m.dst}</span>
                  </div>
                ))}
                {q.data && total === 0 && <div className="mv"><span className="muted">Her şey zaten bu düzende.</span></div>}
              </div>
              <div className="group" style={{ flex: "1 1 240px", padding: "10px 14px", maxHeight: 420, overflowY: "auto" }}>
                <div className="lbl" style={{ marginBottom: 6 }}>Yeni klasörler</div>
                <div style={{ fontSize: 12, lineHeight: 1.9 }}>
                  {q.data?.tree.slice(0, 40).map((t) => (
                    <div key={t.folder} className="row"><span className="grow ellipsis">{t.folder}</span><span className="muted">{fmtInt(t.n)}</span></div>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section>
            <button className="btn ghost" style={{ paddingLeft: 4 }} onClick={() => setMoreOpen((o) => !o)} aria-expanded={moreOpen}>
              <Chevron size={13} style={{ transform: moreOpen ? undefined : "rotate(-90deg)", transition: "transform .15s" }} />Seçenekler
            </button>
            {moreOpen && (
              <div className="group" style={{ marginTop: 6 }}>
                <div className="grow-row">
                  <div className="k">Hangi dosyalar<small>Yeni eklenenler henüz kütüphaneye alınmamış dosyalardır.</small></div>
                  <div className="seg">
                    <button className={cls(scope === "tracked" && "on")} onClick={() => setScope("tracked")}>Kütüphanedekiler</button>
                    <button className={cls(scope === "all" && "on")} onClick={() => setScope("all")}>Yeni eklenenler dahil</button>
                  </div>
                </div>
                <div className="grow-row">
                  <div className="k">Hedef klasör<small>Boş bırakırsan kütüphanenin kendisi kullanılır.</small></div>
                  <div className="field" style={{ width: 200 }}><input value={base} onChange={(e) => setBase(e.target.value)} placeholder="ör. Arşiv" /></div>
                </div>
              </div>
            )}
            <div className="muted" style={{ fontSize: 11.5, margin: "8px 4px 0", lineHeight: 1.5 }}>
              Hiçbir dosya silinmez. Düzenleme dosyaları (.AAE, .XMP) fotoğraflarıyla birlikte taşınır, aynı adlı dosyalara numara eklenir.
              Tarihi olmayanlar “_tarihsiz”, konumu olmayanlar “_konumsuz” klasörüne gider.
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
