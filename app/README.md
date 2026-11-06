# MediaMind masaüstü uygulaması

Tauri 2 + React + TypeScript. Kurulum ve çalıştırma için kök [README](../README.md).

Tarayıcıda geliştirme (çekirdeği elle başlatarak):

```sh
MEDIAMIND_TOKEN=dev ../core/.venv/bin/mediamind serve --port 7399 &
VITE_MM_URL=http://127.0.0.1:7399 VITE_MM_TOKEN=dev npx vite --port 1420
```

## Tek parça uygulama (dağıtım)

```bash
npm run dist     # tauri build + scripts/bundle-core.sh
```

`bundle-core.sh`, derlenen `MediaMind.app` içine şunları gömer (`Contents/Resources/core/`):

- `python/` — uv'nin taşınabilir CPython'u (test/tk kitaplıkları hariç)
- `site-packages/` — `uv.lock`taki sürümlerle çekirdek ve bağımlılıkları (geliştirme araçları hariç)
- `media/` + `bin/ffmpeg`, `bin/ffprobe` — Homebrew'daki ffmpeg, bağımlı dylib'leri `@executable_path/../lib`'e taşınıp yeniden bağlanmış (`scripts/bundle_dylibs.py`)
- `exiftool/` + `bin/exiftool` — saf Perl, macOS'un `/usr/bin/perl`'üyle çalışır
- `bin/mediamind` — başlatıcı (uygulama, MCP ve komut satırı ortak kullanır)

Uygulama önce gömülü çekirdeği arar; yoksa (geliştirme) `core/.venv`'i kullanır. Modeller paketin içinde değildir;
ilk kullanımda `~/Library/Application Support/MediaMind/models`'a indirilir. İmza ad-hoc'tur: başka bir Mac'te ilk
açılışta Finder'da sağ tık › Aç gerekir (Apple Developer ID ile imzalama/noterlik yapılmadı).
