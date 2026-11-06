# MediaMind

Yerel ve harici disklerdeki fotoğraf/video arşivleri için offline arşiv yöneticisi ve görüntüleyici.
Tasarım ve yol haritası: [docs/PLAN.md](docs/PLAN.md).

| Klasör | İçerik |
|---|---|
| [core/](core/) | Python çekirdeği: repo, tarama, analiz, tekrarlar, planlar, HTTP API, `mediamind` CLI |
| [app/](app/) | Tauri 2 + React masaüstü uygulaması |

## Gereksinimler (macOS)

```sh
brew install exiftool ffmpeg rustup uv node
rustup default stable
```

## Uygulamayı çalıştırma

```sh
cd core && uv sync            # çekirdeği kur (core/.venv)
cd ../app && npm install
npm run tauri dev             # geliştirme modunda aç
npm run tauri build           # app/src-tauri/target/release/bundle/macos/MediaMind.app üretir
```

Uygulama açılırken çekirdeği (`core/.venv/bin/mediamind serve`) boş bir portta ve rastgele bir oturum token'ıyla
kendisi başlatır, kapanırken durdurur. Başka bir çekirdek kullanmak için `MEDIAMIND_CORE=/yol/mediamind`.

> Not: Şimdilik derlenen `.app`, çekirdeği bu depodaki `core/.venv` içinden çalıştırır (Python gömülü değil).
> Dağıtım için Python çalışma zamanının pakete gömülmesi ileriki bir adım.

## Komut satırı

```sh
cd /Volumes/T7/Aile && mediamind init && mediamind status && mediamind track
```

Ayrıntılar: [core/README.md](core/README.md).
