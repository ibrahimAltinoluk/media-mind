import { useEffect, useState } from "react";
import { cls } from "../util";
import appIcon from "../assets/app-icon.png";
import { FolderTree, People, Pin, Shield, Sparkles } from "./Icons";

/** Uygulama simgesi (Dock'taki ile aynı) */
export function AppIcon({ size }: { size: number }) {
  return <img src={appIcon} width={size} height={size} alt="" draggable={false} style={{ display: "block" }} />;
}

const KEY = "mm.onboarded.v1";

const STEPS = [
  {
    icon: <AppIcon size={64} />,
    title: "MediaMind'a hoş geldin",
    body: "Fotoğraf ve videolarını tarih, yer, kişi ve içeriğe göre düzenler; tekrarları ve gereksizleri bulur. Her şey bu Mac'te çalışır.",
    points: ["Dosyaların yerinde kalır, hiçbiri değiştirilmez.", "Bilgiler kütüphane klasöründeki gizli .mediamind dizininde tutulur; harici diski başka bir Mac'e taksan da tanınır.",
      "Klasörde eklediğin, sildiğin ya da taşıdığın dosyalar birkaç saniyede fark edilir."],
  },
  {
    icon: <Sparkles size={44} />,
    title: "Akıllı analiz",
    body: "Ayarlar › Akıllı analiz'den modelleri bir kez indirince MediaMind fotoğraflarını tanımaya başlar.",
    points: ["“Kumsalda köpek”, “doğum günü pastası” gibi aramalar", "Kişileri tanıma; bir yüze ad verince benzerleri de etiketlenir",
      "Fotoğraftaki yazıyı okuma (“fatura”, “pasaport”), videolarda sahneler ve konuşmalar"],
  },
  {
    icon: <Pin size={44} />,
    title: "Gezinme",
    body: "Kenar çubuğu her şeye bir tık uzaklıkta.",
    points: ["Anılar: geçmiş yıllarda bugün, geziler", "Yerler: haritada fotoğrafların; Klasörler: Finder gibi gezinme",
      "Albümler: elle ya da süzgece göre kendiliğinden dolan akıllı albümler", "Fotoğraflara sağ tıkla: albüme ekle, tarihi düzelt, dışa aktar, sil"],
  },
  {
    icon: <FolderTree size={44} />,
    title: "Düzen ve temizlik",
    body: "Araçlar bölümü yer açmana ve arşivini toparlamana yardım eder.",
    points: ["Temizlik: ekran görüntüleri, bulanık ve kazara çekilmiş fotoğraflar, küçültülebilecek videolar", "Tekrarlar: aynı ya da neredeyse aynı kopyalar",
      "Klasörleri düzenle: yıl/ay, şehir, kişi gibi düzenlere taşıma"],
  },
  {
    icon: <Shield size={44} />,
    title: "Hiçbir şey sormadan silinmez",
    body: "Silme ve taşıma her zaman önce onayına gelir.",
    points: ["Silinenler Son silinenler'de bekler; geri getirebilirsin", "Geçmiş'te her değişiklik durur ve geri alınabilir",
      "Asistan (⌘J) arşivinle ilgili soruları yanıtlar; değişiklik önerirse yine sen onaylarsın"],
  },
];

/** İlk açılış tanıtımı (5 adım). Ayarlar › Genel'den yeniden açılabilir. */
export function Onboarding() {
  const [open, setOpen] = useState(() => { try { return !localStorage.getItem(KEY); } catch { return false; } });
  const [i, setI] = useState(0);
  useEffect(() => {
    const show = () => { setI(0); setOpen(true); };
    window.addEventListener("mm:onboarding", show);
    return () => window.removeEventListener("mm:onboarding", show);
  }, []);
  const close = () => {
    setOpen(false);
    try { localStorage.setItem(KEY, "1"); } catch { /* yok */ }
  };
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight" || e.key === "Enter") setI((x) => (x < STEPS.length - 1 ? x + 1 : (close(), x)));
      else if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
      else return;
      e.preventDefault();
      e.stopImmediatePropagation();
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [open]);
  if (!open) return null;
  const s = STEPS[i];
  const last = i === STEPS.length - 1;
  return (
    <div className="overlay" style={{ alignItems: "center", zIndex: 95 }}>
      <section className="dialog onboard" role="dialog" aria-modal="true" aria-labelledby="ob-title">
        <div className="ob-icon">{s.icon}</div>
        <h2 id="ob-title">{s.title}</h2>
        <p>{s.body}</p>
        <ul>{s.points.map((p) => <li key={p}>{p}</li>)}</ul>
        <div className="ob-dots" aria-hidden="true">{STEPS.map((_, k) => <span key={k} className={cls(k === i && "on")} />)}</div>
        <footer>
          {!last && <button className="btn ghost" onClick={close}>Geç</button>}
          <span className="grow" />
          {i > 0 && <button className="btn" onClick={() => setI(i - 1)}>Geri</button>}
          <button className="btn pri" onClick={() => (last ? close() : setI(i + 1))} autoFocus>{last ? "Başla" : "Devam"}</button>
        </footer>
      </section>
    </div>
  );
}

export const showOnboarding = () => window.dispatchEvent(new Event("mm:onboarding"));
export const requestAddFolder = () => window.dispatchEvent(new Event("mm:add-folder"));

/** Kütüphane yokken: tek büyük “Klasör ekle” düğmesi ve kısa açıklama. */
export function WelcomeHero() {
  return (
    <div className="empty" style={{ maxWidth: 560, margin: "40px auto", gap: 12 }}>
      <AppIcon size={96} />
      <h2 style={{ fontSize: 24 }}>Fotoğraflarını ekleyerek başla</h2>
      <span>Fotoğraf ve videolarının bulunduğu klasörü seç. Harici bir disk de olabilir; istediğin kadar kütüphane ekleyebilirsin.</span>
      <button className="btn pri lg" onClick={requestAddFolder}>Klasör ekle…</button>
      <span style={{ fontSize: 12 }}>Dosyaların yerinde kalır. MediaMind bilgilerini o klasördeki gizli <span className="mono">.mediamind</span> dizininde tutar.</span>
      <div className="row wrap" style={{ gap: 16, marginTop: 10, justifyContent: "center", fontSize: 12 }}>
        <span className="row" style={{ gap: 6 }}><People size={16} />Kişileri tanır</span>
        <span className="row" style={{ gap: 6 }}><Pin size={16} />Haritada gösterir</span>
        <span className="row" style={{ gap: 6 }}><Sparkles size={16} />İçerikle arar</span>
      </div>
      <button className="btn ghost sm" onClick={showOnboarding}>Nasıl çalışır?</button>
    </div>
  );
}
