//! MediaMind masaüstü kabuğu: pencere, klasör seçici ve Python çekirdeğinin yaşam döngüsü.

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent};

#[derive(Clone, Serialize)]
struct BackendInfo {
    url: String,
    token: String,
}

struct Backend {
    info: BackendInfo,
    child: Mutex<Option<Child>>,
    spawn_error: Option<String>,
}

/// Çekirdek CLI'nin yolu: MEDIAMIND_CORE ortam değişkeni; yoksa uygulamanın içine gömülü çekirdek
/// (Contents/Resources/core/bin/mediamind); o da yoksa (geliştirme) depodaki core/.venv.
fn core_binary(resources: Option<PathBuf>) -> PathBuf {
    if let Ok(p) = std::env::var("MEDIAMIND_CORE") {
        return PathBuf::from(p);
    }
    if let Some(r) = resources {
        let bundled = r.join("core/bin/mediamind");
        if bundled.exists() {
            return bundled;
        }
    }
    PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../core/.venv/bin/mediamind"))
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(7341)
}

/// Çekirdeğin çıktısı buraya yazılır; açılmazsa hata ekranında son satırları gösterilir.
fn log_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(home).join("Library/Logs/MediaMind/core.log")
}

fn start_backend(resources: Option<PathBuf>) -> Backend {
    let port = free_port();
    let token = uuid::Uuid::new_v4().simple().to_string();
    let bin = core_binary(resources);
    // exiftool/ffmpeg Homebrew'dan gelir; Finder'dan açılan uygulamanın PATH'i kısadır
    let path = format!(
        "/opt/homebrew/bin:/usr/local/bin:{}",
        std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin".into())
    );
    let log = log_path();
    if let Some(dir) = log.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let (out, err) = match std::fs::File::create(&log).and_then(|f| Ok((f.try_clone()?, f))) {
        Ok((a, b)) => (Stdio::from(a), Stdio::from(b)),
        Err(_) => (Stdio::inherit(), Stdio::inherit()),
    };
    let child = Command::new(&bin)
        .args(["serve", "--port", &port.to_string()])
        .env("MEDIAMIND_TOKEN", &token)
        .env("PATH", path)
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err)
        .spawn();
    // Hazır olmasını burada beklemeyiz (pencere hemen açılsın); arayüz /health'i yoklar,
    // çekirdek kapanırsa backend_status ile nedenini öğrenir. Eski/yavaş Mac'te ilk açılış uzun sürebilir.
    let (child, spawn_error) = match child {
        Ok(c) => (Some(c), None),
        Err(e) => (None, Some(format!("Çekirdek başlatılamadı ({}): {e}", bin.display()))),
    };
    Backend {
        info: BackendInfo { url: format!("http://127.0.0.1:{port}"), token },
        child: Mutex::new(child),
        spawn_error,
    }
}

#[derive(Clone, Serialize)]
struct BackendStatus {
    running: bool,
    error: Option<String>,
    log_path: String,
    log_tail: String,
}

/// Çekirdek hâlâ çalışıyor mu; kapandıysa çıkış kodu ve günlüğün son satırları.
#[tauri::command]
fn backend_status(state: tauri::State<Backend>) -> BackendStatus {
    let log = log_path();
    let tail = || {
        let text = std::fs::read_to_string(&log).unwrap_or_default();
        let lines: Vec<&str> = text.lines().collect();
        lines[lines.len().saturating_sub(25)..].join("\n")
    };
    let mut guard = state.child.lock().unwrap();
    let (running, error) = match guard.as_mut() {
        None => (false, state.spawn_error.clone().or_else(|| Some("Çekirdek çalışmıyor".into()))),
        Some(c) => match c.try_wait() {
            Ok(None) => (true, None),
            Ok(Some(code)) => (false, Some(format!("Çekirdek kapandı ({code})"))),
            Err(e) => (false, Some(e.to_string())),
        },
    };
    BackendStatus { running, error, log_path: log.display().to_string(), log_tail: if running { String::new() } else { tail() } }
}

#[tauri::command]
fn backend_info(state: tauri::State<Backend>) -> BackendInfo {
    state.info.clone()
}

/// macOS paylaşım menüsünü (AirDrop, Mesajlar, Mail, Notlar…) verilen dosyalarla, pencerede (x, y) noktasında açar.
#[tauri::command]
fn share_files(window: tauri::WebviewWindow, paths: Vec<String>, x: f64, y: f64) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let ptr = window.ns_view().map_err(|e| e.to_string())? as usize;
        window
            .run_on_main_thread(move || unsafe {
                use objc2::rc::Retained;
                use objc2::runtime::AnyObject;
                use objc2::AnyThread;
                use objc2_app_kit::{NSSharingServicePicker, NSView};
                use objc2_foundation::{NSArray, NSPoint, NSRect, NSRectEdge, NSSize, NSString, NSURL};

                let view: &NSView = &*(ptr as *const NSView);
                let urls: Vec<Retained<AnyObject>> = paths
                    .iter()
                    .map(|p| Retained::into_super(Retained::into_super(NSURL::fileURLWithPath(&NSString::from_str(p)))))
                    .collect();
                let items = NSArray::from_retained_slice(&urls);
                let picker = NSSharingServicePicker::initWithItems(NSSharingServicePicker::alloc(), &items);
                let h = view.frame().size.height;
                let yy = if view.isFlipped() { y } else { h - y };
                let rect = NSRect::new(NSPoint::new(x, yy), NSSize::new(1.0, 1.0));
                picker.showRelativeToRect_ofView_preferredEdge(rect, view, NSRectEdge::MinY);
                // Menü açıkken nesne yaşamalı; kapanınca AppKit bırakır (küçük, kasıtlı sızıntı)
                std::mem::forget(picker);
            })
            .map_err(|e| e.to_string())?;
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (window, paths, x, y);
        Err("Paylaşım yalnız macOS'ta destekleniyor".into())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            app.manage(start_backend(app.path().resource_dir().ok()));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_info, backend_status, share_files])
        .build(tauri::generate_context!())
        .expect("MediaMind başlatılamadı");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<Backend>() {
                if let Some(mut child) = state.child.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    });
}
