import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BackendError, initBackend, isTauri } from "./api";
import App from "./App";
import { AppProvider } from "./state";
import "./styles.css";

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 60_000, gcTime: 30 * 60_000 } } });
const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement);

// Masaüstü kabuğunda pencere düğmeleri (trafik ışıkları) için yer bırakılır
document.documentElement.dataset.shell = isTauri ? "tauri" : "web";

// Tema, çekirdek hazır olmadan da doğru görünsün
try {
  document.documentElement.dataset.theme = JSON.parse(localStorage.getItem("mm.theme") ?? '"system"');
} catch {
  document.documentElement.dataset.theme = "system";
}

root.render(<div className="empty" style={{ height: "100%" }}>MediaMind başlatılıyor…</div>);
let slowShown = false;
initBackend(() => {
  if (slowShown) return;
  slowShown = true;
  root.render(
    <div className="empty" style={{ height: "100%" }}>
      <span className="spin" />
      <h2>MediaMind başlatılıyor…</h2>
      <span>İlk açılış eski Mac'lerde birkaç dakika sürebilir; macOS uygulamanın dosyalarını bir kez denetliyor.</span>
    </div>,
  );
})
  .then(() =>
    root.render(
      <React.StrictMode>
        <QueryClientProvider client={qc}>
          <AppProvider>
            <App />
          </AppProvider>
        </QueryClientProvider>
      </React.StrictMode>,
    ),
  )
  .catch((e) => {
    const err = e as BackendError;
    root.render(
      <div className="empty" style={{ height: "100%", padding: 24 }}>
        <h2>Çekirdeğe bağlanılamadı</h2>
        <span>{String(err.message)}</span>
        {err.detail && (
          <pre className="selectable" style={{ maxWidth: "min(760px, 92vw)", maxHeight: "45vh", overflow: "auto", textAlign: "left", fontSize: 11, padding: 10, borderRadius: 8, background: "var(--panel-2)", whiteSpace: "pre-wrap" }}>{err.detail}</pre>
        )}
        {err.logPath && <span className="muted selectable" style={{ fontSize: 12 }}>Ayrıntılı günlük: {err.logPath}</span>}
      </div>,
    );
  });
