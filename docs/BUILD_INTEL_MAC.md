# MediaMind: Intel (2012+) Mac ve Çoklu Mimari Dağıtım Rehberi

Bu rehber, MediaMind uygulamasının **2012 yılında üretilmiş eski Intel Mac'ler (MacBook Pro, iMac, Mac mini vb.)** için nasıl derleneceğini, paketleneceğini ve yeni Apple Silicon (M1–M4) sürümleriyle birlikte nasıl yönetileceğini açıklar.

---

## 1. 2012 Mac Donanım & Yazılım Kısıtları

2012 model Mac'ler (örneğin MacBook Pro Mid 2012, Mac mini Late 2012, iMac Late 2012):
* **İşlemci Mimarisi:** 64-bit Intel (`x86_64`), 3. Nesil Intel Core (Ivy Bridge - AVX 1.0, SSE4.2).
* **Maksimum Resmi macOS Sürümü:** **macOS 10.15 Catalina** (10.15.7). *(OpenCore Legacy Patcher / OCLP ile Big Sur, Monterey, Ventura, Sonoma veya Sequoia çalıştırılabilir).*
* **Tauri 2 Uyumluluğu:** Tauri 2'nin kullandığı WKWebView API'leri macOS 10.15 Catalina ve üzerini gerektirir. Bu nedenle derleme hedefimiz `MACOSX_DEPLOYMENT_TARGET=10.15` olarak sabitlenmiştir.

---

## 2. Eski ve Yeni Build'leri Yönetme (Build Sistemi)

Projede hem Intel hem de Apple Silicon build'lerini yönetmek için `scripts/build.sh` geliştirilmiştir. Çıktılar birbirinin üzerine yazılmaz; `dist-releases/` klasöründe ayrı ayrı saklanır:

```
dist-releases/
├── intel-x86_64/
│   ├── MediaMind.app
│   └── MediaMind-v0.1.0-macos-intel-x86_64.zip
└── apple-silicon-arm64/
    ├── MediaMind.app
    └── MediaMind-v0.1.0-macos-arm64.zip
```

### Hızlı Komutlar

Proje kök dizininde veya `app/` klasörü içinde çalıştırabilirsiniz:

| Komut | Açıklama |
|---|---|
| `./scripts/build.sh intel` *(veya `npm run dist:intel`)* | 2012+ Intel Mac için x86_64 paketini derler. |
| `./scripts/build.sh arm64` *(veya `npm run dist:arm64`)* | Apple Silicon (M1/M2/M3/M4) paketini derler. |
| `./scripts/build.sh all` *(veya `npm run dist:all`)* | Her iki mimariyi de sırayla derler ve paketler. |
| `./scripts/build.sh list` *(veya `npm run dist:list`)* | Mevcut build'lerin mimarisini, boyutunu ve durumunu listeler. |
| `./scripts/build.sh clean` | `dist-releases` ve geçici hedef önbelleklerini temizler. |

---

## 3. Intel (x86_64) Paketi Nasıl Çalışır?

Tek parça `.app` oluşturulurken 4 temel bileşen Intel için özel olarak hazırlanır:

1. **Tauri Kabuğu (`mediamind-app`):**
   `cargo build --target x86_64-apple-darwin --release` ile derlenir (`minos: 10.15`).
2. **Gömülü Python Çekirdeği:**
   `uv` aracılığıyla taşınabilir `cpython-3.12-macos-x86_64-none` temin edilir. Python paketleri (`site-packages`), Intel mimarisine uygun tekerlekler (wheels) seçilerek içine kurulur.
3. **Statik FFmpeg / FFprobe:**
   Apple Silicon Homebrew'ındaki arm64 dylib'ler Intel'de çalışmayacağı için, Intel paketinde `evermeet.cx` tarafından sağlanan bağımsız statik x86_64 ikilileri kullanılır.
4. **ExifTool:**
   Saf Perl olarak paketlenir; macOS sistem `/usr/bin/perl` yorumlayıcısı ile doğal olarak evrensel çalışır.
5. **Kod İmzalama (Ad-hoc):**
   `codesign --force --deep --sign -` ile paket kilitlenir.

---

## 4. 2012 Intel Mac'e Kurulum ve İlk Açılış

1. `dist-releases/intel-x86_64/MediaMind-v0.1.0-macos-intel-x86_64.zip` dosyasını USB bellek, AirDrop veya yerel ağ üzerinden 2012 Mac'inize aktarın.
2. ZIP dosyasını açın ve çıkan `MediaMind.app` uygulamasını **/Applications** (Uygulamalar) klasörüne taşıyın.
3. **Apple Gatekeeper Uyarısı:**
   Uygulama ücretli Apple Geliştirici Sertifikası olmadan ad-hoc imzalandığından, macOS ilk açılışta *"MediaMind geliştiricisi doğrulanamadığı için açılamıyor"* diyebilir.
   
   Bunu aşmanın 2 yolu vardır:
   * **Yöntem 1 (Arayüz):** Uygulama simgesine **Sağ Tık (veya Control + Tık)** yapın -> **Aç**'ı seçin -> Açılan onay penceresinde tekrar **Aç** butonuna basın. (macOS bu tercihi hatırlar).
   * **Yöntem 2 (Terminal):**
     ```bash
     xattr -cr /Applications/MediaMind.app
     ```

---

## 5. Sorun Giderme

* **Rust Hedefi Eksik Hatası:**
  Derleme yaptığınız makinede şu komutu çalıştırın:
  ```bash
  rustup target add x86_64-apple-darwin
  ```
* **Python / FFmpeg İndirme Kısıtı:**
  Script ilk Intel derlemesinde x86_64 Python ve FFmpeg ikililerini indirir ve `core/.cache/bin/x86_64/` altında önbelleğe alır. Sonraki derlemelerde internet bağlantısı gerekmez.

