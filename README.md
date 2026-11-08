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

## Uygulamayı çalıştırma ve dağıtım paketleri

```sh
cd core && uv sync            # çekirdeği kur (core/.venv)
cd ../app && npm install
npm run tauri dev             # geliştirme modunda aç
```

### Çoklu Mimari Dağıtım Paketleri (Intel & Apple Silicon)

Hem eski 2012+ Intel Mac'ler (`x86_64`, macOS 10.15+) hem de yeni Apple Silicon (`arm64`, M1–M4) için paket oluşturma:

```sh
./scripts/build.sh intel       # 2012+ Intel Mac için bağımsız paket üretir
./scripts/build.sh arm64       # Apple Silicon Mac için paket üretir
./scripts/build.sh all         # İki mimari için sırayla üretir
./scripts/build.sh list        # Üretilen paketleri ve mimarilerini listeler
```

Çıktılar `dist-releases/intel-x86_64/` ve `dist-releases/apple-silicon-arm64/` klasörlerine `.app` ve taşımaya hazır `.zip` formatında yerleştirilir. Ayrıntılar için: [docs/BUILD_INTEL_MAC.md](docs/BUILD_INTEL_MAC.md).

## Komut satırı

```sh
cd /Volumes/T7/Aile && mediamind init && mediamind status && mediamind track
```

Ayrıntılar: [core/README.md](core/README.md).
