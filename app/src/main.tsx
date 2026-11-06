import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { initBackend, isTauri } from "./api";
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
initBackend()
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
  .catch((e) => root.render(<div className="empty" style={{ height: "100%" }}><h2>Çekirdeğe bağlanılamadı</h2><span>{String(e.message)}</span></div>));
