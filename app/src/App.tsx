import { BusyPill } from "./components/BusyPill";
import { useEffect, useState } from "react";
import { Onboarding, WelcomeHero } from "./components/Onboarding";
import { AssistantDrawer } from "./components/Assistant";
import { PlanDialog, Toasts } from "./components/PlanDialog";
import { Sidebar } from "./components/Sidebar";
import { Viewer } from "./components/Viewer";
import { Duplicates } from "./pages/Duplicates";
import { Library } from "./pages/Library";
import { History, Placeholder, Places } from "./pages/Misc";
import { TrashPage } from "./pages/Trash";
import { Memories } from "./pages/Memories";
import { Cleanup } from "./pages/Cleanup";
import { ImportPage } from "./pages/Import";
import { Settings } from "./pages/Settings";
import { Toolbar } from "./components/Toolbar";
import { Organize } from "./pages/Organize";
import { People } from "./pages/People";
import { useApp, type Page } from "./state";
import { cls } from "./util";

/** Sayfa değişince kapanmayan (canlı tutulan) sayfalar */
const KEEP: Page[] = ["library", "people"];
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

function Welcome() {
  return (
    <>
      <Toolbar title="" />
      <div className="scroll"><WelcomeHero /></div>
    </>
  );
}

export default function App() {
  const { repoId, page, setPage, assistantOpen, toggleAssistant, trackJob } = useApp();
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos });

  // Repo açıldığında bir kez değişiklikleri tara
  useEffect(() => {
    if (!repoId) return;
    const key = `mm.scanned.${repoId}`;
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, "1");
    api.scan(repoId).then(trackJob).catch(() => sessionStorage.removeItem(key));
  }, [repoId, trackJob]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.metaKey && e.key.toLowerCase() === "j") {
        e.preventDefault();
        toggleAssistant();
      }
      if (e.metaKey && e.key === ",") {
        e.preventDefault();
        setPage("settings");
      }
      if (e.metaKey && (e.key.toLowerCase() === "k" || e.key.toLowerCase() === "f")) {
        const el = [...document.querySelectorAll<HTMLInputElement>(".search input")].find((x) => !x.closest(".off"));
        if (el) {
          e.preventDefault();
          el.focus();
        }
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [toggleAssistant, setPage]);

  const cur = repos.data?.find((r) => r.id === repoId);
  const liveRepo = page !== "settings" && page !== "assistant" && repoId && cur?.online ? repoId : null;
  const [visited, setVisited] = useState<{ repo: string | null; pages: Set<Page> }>({ repo: null, pages: new Set() });
  useEffect(() => {
    setVisited((v) => {
      const pages = v.repo === repoId ? v.pages : new Set<Page>();
      if (!KEEP.includes(page) || pages.has(page)) return v.repo === repoId ? v : { repo: repoId, pages };
      return { repo: repoId, pages: new Set(pages).add(page) };
    });
  }, [page, repoId]);
  let body: React.ReactNode;
  if (page === "settings" || page === "assistant") body = <Settings key={page} initial={page === "assistant" ? "assistant" : "general"} />;
  else if (!repoId || !cur) body = <Welcome />;
  else if (!cur.online)
    body = <Placeholder title={`“${cur.name}” bağlı değil`}>{cur.last_path} bulunamadı. Diski taktığında kütüphane kendiliğinden açılır.</Placeholder>;
  else if (KEEP.includes(page)) body = null; // aşağıda canlı tutulan sayfalardan biri gösterilir
  else
    body = {
      library: <Library />,
      places: <Places />,
      people: <People />,
      dups: <Duplicates />,
      organize: <Organize />,
      history: <History />,
      trash: <TrashPage />,
      memories: <Memories />,
      cleanup: <Cleanup />,
      import: <ImportPage />,
    }[page];

  return (
    <div className="app">
      <Sidebar />
      <div className="main" style={{ position: "relative" }}>
        {body}
        {/* Kütüphane ve Kişiler sayfa değiştirince kapanmaz: kaydırma konumu, seçim ve yüklenmiş veri korunur */}
        {liveRepo && KEEP.filter((p) => visited.repo === liveRepo && (visited.pages.has(p) || p === page)).map((p) => (
          <div key={`${liveRepo}:${p}`} className={cls("page-slot", page !== p && "off")} aria-hidden={page !== p}>
            {p === "library" ? <Library /> : <People />}
          </div>
        ))}
      </div>
      <AssistantDrawer hidden={!assistantOpen} />
      <Viewer />
      <PlanDialog />
      <Toasts />
      <BusyPill />
      <Onboarding />
    </div>
  );
}
