import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { api, faceUrl } from "../api";
import { fmtInt } from "../util";
import { Plus } from "./Icons";
import { Popover } from "./Popover";

/**
 * Kişi seçici: var olan kişiler arasında arar (avatar + fotoğraf sayısı), eşleşme yoksa yeni kişi oluşturmayı önerir.
 * Klavye: ↑/↓ gez, Enter seç, Escape vazgeç.
 */
export function PersonPicker({ repoId, onPick, onCancel, placeholder = "Bu kim?", autoFocus, initial = "", exclude = [], allowCreate = true, height = 34 }: {
  repoId: string;
  onPick: (name: string) => void;
  onCancel?: () => void;
  placeholder?: string;
  autoFocus?: boolean;
  initial?: string;
  exclude?: number[];
  allowCreate?: boolean;
  height?: number;
}) {
  const people = useQuery({ queryKey: ["people", repoId], queryFn: () => api.people(repoId) });
  const [text, setText] = useState(initial);
  const [open, setOpen] = useState(!!autoFocus);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLLabelElement>(null);

  const q = text.trim().toLocaleLowerCase("tr");
  const options = useMemo(() => {
    const persons = (people.data?.persons ?? []).filter((p) => !exclude.includes(p.id));
    const hits = persons
      .filter((p) => !q || p.name.toLocaleLowerCase("tr").includes(q))
      .sort((a, b) => {
        // Başı eşleşenler önce, sonra fotoğraf sayısı
        const sa = a.name.toLocaleLowerCase("tr").startsWith(q) ? 0 : 1;
        const sb = b.name.toLocaleLowerCase("tr").startsWith(q) ? 0 : 1;
        return sa - sb || b.photos - a.photos;
      })
      .slice(0, 30)
      .map((p) => ({ kind: "person" as const, name: p.name, cover: p.cover, photos: p.photos }));
    const exact = persons.some((p) => p.name.toLocaleLowerCase("tr") === q);
    const create = allowCreate && q && !exact ? [{ kind: "create" as const, name: text.trim(), cover: null, photos: 0 }] : [];
    return [...hits, ...create];
  }, [people.data, q, text, exclude, allowCreate]);

  const pick = (name: string) => {
    if (!name.trim()) return;
    setOpen(false);
    setText("");
    onPick(name.trim());
  };

  const onKey = (e: React.KeyboardEvent) => {
    e.stopPropagation(); // görüntüleyici/kütüphane kısayollarını tetiklemesin
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(options.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); const o = options[active]; if (o) pick(o.name); else if (allowCreate) pick(text); }
    else if (e.key === "Escape") { e.preventDefault(); setOpen(false); onCancel?.(); }
  };

  return (
    <>
      <label ref={wrapRef} className="field" style={{ height, flex: 1 }}>
        <span className="sr">{placeholder}</span>
        <input ref={inputRef} autoFocus={autoFocus} value={text} placeholder={placeholder}
          onChange={(e) => { setText(e.target.value); setActive(0); setOpen(true); }}
          onFocus={() => setOpen(true)} onKeyDown={onKey} />
      </label>
      {open && options.length > 0 && (
        <Popover anchor={wrapRef.current} onClose={() => setOpen(false)} width={Math.max(240, wrapRef.current?.offsetWidth ?? 0)} align="start">
          {options.map((o, i) => (
            <button key={`${o.kind}-${o.name}`} className="mitem" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(o.name)}
              onMouseEnter={() => setActive(i)} style={i === active ? { background: "var(--hover)" } : undefined}>
              {o.kind === "person" ? (
                <span style={{ width: 24, height: 24, borderRadius: "50%", overflow: "hidden", background: "var(--tile-bg)", flex: "none" }}>
                  {o.cover && <img src={faceUrl(repoId, o.cover)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
                </span>
              ) : (
                <Plus size={16} style={{ color: "var(--accent)", margin: "0 4px" }} />
              )}
              <span className="grow ellipsis" style={o.kind === "create" ? { color: "var(--accent)", fontWeight: 600 } : undefined}>
                {o.kind === "create" ? `Yeni kişi: “${o.name}”` : o.name}
              </span>
              {o.kind === "person" && <span className="muted" style={{ fontSize: 12 }}>{fmtInt(o.photos)}</span>}
            </button>
          ))}
        </Popover>
      )}
    </>
  );
}
