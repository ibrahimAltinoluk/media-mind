# MediaMind — Teknik Plan

> Durum: taslak v1 · 2026-11-05
> Arayüz taslağı: https://claude.ai/artifact/4TbeYA7UNUtXJd2UL1HEwU (onaylandı)

## 1. Amaç

MediaMind, yerel ve harici disklerdeki fotoğraf ve video arşivlerini yöneten bir macOS uygulamasıdır. Uygulama:

- Arşivi **git'e benzer bir repo** olarak tanır. Her repo kendi kökündeki `.mediamind/` klasöründe yaşar.
- Dosyaları meta veriye (tarih, konum, kamera) ve içeriğe (kişiler, hayvanlar, nesneler, ortam) göre etiketler.
- Birebir aynı, neredeyse aynı ve benzer dosyaları bulur, hangisinin tutulacağını önerir, onayla siler.
- Dosyaları şablonlara göre dizinlere yerleştirir (`{yıl}/{ay}` vb.).
- Günlük kullanım için hızlı bir **fotoğraf görüntüleyici ve video oynatıcıdır**.
- **MCP sunucusu** ve yerleşik asistan sayesinde doğal dille yönetilebilir.
- Analizlerin tamamı **offline** çalışır. Fotoğraflar makineden çıkmaz.

## 2. Alınmış kararlar

| Konu | Karar |
|---|---|
| Platform | macOS (Apple Silicon). Windows ileride olabilir; mimari onu engellememeli. |
| Medya | Fotoğraf ve video (HEIC, JPEG, PNG, RAW, MOV, MP4 ve diğerleri). |
| Repo modeli | Her repo bağımsız. Kişiler, etiketler ve ayarlar repolar arasında paylaşılmaz. |
| Merkez | Bir repoya ait her şey `<kök>/.mediamind/` içindedir. Uygulama yalnızca repo listesini global olarak tutar. |
| Repo yönetimi | "Klasörü repo yap" klasör seçiciyle yapılır. Repolar menüde listelenir. "Menüden kaldır" dosyalara ve `.mediamind` klasörüne dokunmaz. |
| Silme ve taşıma | Her zaman **plan → onay → uygula** sırasıyla yapılır. Silinen dosya önce repo çöpüne gider ve geri alınabilir. |
| Tekrarda tutulacak dosya | Varsayılan olarak en yüksek kalite (çözünürlük + tam EXIF). Eşitlikte kullanıcının "ana klasör"ündeki kopya tutulur. Kural değiştirilebilir. |
| Arayüz | Detay paneli tıklayınca açılır. Asistan her sayfada bir yan paneldir (⌘J). Açık ve koyu tema vardır, varsayılan sistem temasını izler. |

## 3. Mimari

```
┌──────────────────────────── MediaMind.app (Tauri 2) ────────────────────────────┐
│  React + TypeScript arayüz (WebKit)                                              │
│   kütüphane · görüntüleyici · video · kişiler · tekrarlar · düzenle · asistan    │
│        │  HTTP + WebSocket (127.0.0.1, oturum token'ı)                           │
│  ┌─────▼──────────────────────────────────────────────────────────────────────┐  │
│  │  mediamind çekirdeği (Python, arka plan servisi olarak başlatılır)          │  │
│  │   repo · tarama · iş kuyruğu · analiz · arama · planlar · API · MCP          │  │
│  └─────┬───────────────────────────────┬──────────────────────────────────────┘  │
│        │                               │                                         │
│  Rust tarafı: pencere, menü,           │                                         │
│  klasör seçici, Keychain,              │                                         │
│  servis yaşam döngüsü                  │                                         │
└────────────────────────────────────────┼─────────────────────────────────────────┘
                                         │
      ┌──────────────────────────────────┼──────────────────────┐
      ▼                                  ▼                      ▼
 /Volumes/T7/Aile/.mediamind      ~/Pictures/x/.mediamind     ...  (repo başına)

 Diğer istemciler:  `mediamind` CLI  ·  `mediamind mcp` (Claude Desktop / Claude Code)
```

- **Tek motor.** Arayüz, CLI ve MCP aynı Python çekirdeğini kullanır, iş mantığı tek yerde durur.
- **Eşzamanlı erişim.** Bir repoya aynı anda yalnızca bir yazıcı süreç erişir; `.mediamind/lock` dosya kilidiyle sağlanır. Arayüz açıkken MCP ve CLI çalışan servise bağlanır, yoksa çekirdeği kendileri başlatır.
- **Neden Python?** Görüntü modelleri (ONNX Runtime + CoreML), ffmpeg ve exiftool entegrasyonu ile vektör arama bu ekosistemde en olgun hâlinde.
- **Neden Tauri?** Electron'a göre çok daha hafif. Yerel klasör seçici, menü, kısayollar ve Keychain erişimi hazır geliyor. WebKit, macOS'ta HEVC videoyu doğrudan oynatıyor.

## 4. Kod tabanı yapısı

```
media-mind/
├─ core/                         Python çekirdeği (uv ile yönetilir)
│  ├─ pyproject.toml
│  ├─ src/mediamind/
│  │  ├─ cli.py                  mediamind init|status|scan|track|dups|organize|plan|undo|serve|mcp
│  │  ├─ repo/                   repo bulma, repo.toml, kilit, global repo listesi
│  │  ├─ db/                     şema, migration'lar, sorgular (SQLite + sqlite-vec + FTS5)
│  │  ├─ scan/                   dosya yürüyüşü, ignore kuralları, durum tespiti, FSEvents izleyici
│  │  ├─ media/                  exif, tarih çıkarımı, geocode, önizleme üretimi, video (ffprobe/ffmpeg)
│  │  ├─ dedup/                  hash, pHash, grup oluşturma, tutulacak dosya kuralları
│  │  ├─ analysis/               iş kuyruğu, CLIP etiket/embedding, yüz tespiti/kümeleme, sahne, döküm
│  │  ├─ search/                 filtre + metin + anlamsal arama, sıralama
│  │  ├─ plans/                  plan modeli, prova, uygula, geri al, çöp
│  │  ├─ organize/               şablon motoru
│  │  ├─ api/                    FastAPI uygulaması, medya sunumu (range request), WebSocket olayları
│  │  ├─ mcp/                    MCP araçları ve izin katmanı
│  │  └─ assistant/              LLM sağlayıcı soyutlaması, araç döngüsü
│  └─ tests/
├─ app/                          Tauri 2 + React
│  ├─ src-tauri/                 Rust: pencere, menü, servis başlatma, Keychain, dialog
│  └─ src/                       React + TS: sayfalar, bileşenler, tema token'ları
└─ docs/
   └─ PLAN.md
```

## 5. Disk yerleşimi

### 5.1 Repo içi: `.mediamind/`

```
<kök>/
├─ .mediamind/
│  ├─ repo.toml          repo kimliği (UUID), ad, şema sürümü, ayarlar
│  ├─ index.db           SQLite (WAL modunda): tüm meta veri, etiket, yüz, plan, iş
│  ├─ thumbs/ab/cd/      önizlemeler: <blake3>_s.webp (256px), _m.webp (1024px); _l (2560px) talep üzerine
│  ├─ proxies/           doğrudan oynatılamayan videolar için H.264 önizleme kopyaları
│  ├─ faces/             yüz kırpımları (kişi kartları ve küme önizlemeleri için)
│  ├─ trash/<plan_id>/   silinen dosyalar, göreli yolları korunarak
│  ├─ logs/              servis ve analiz günlükleri
│  └─ lock               yazıcı süreç kilidi
├─ .mediamindignore      (isteğe bağlı) .gitignore söz dizimiyle hariç tutulacak yollar
└─ ... kullanıcının dosyaları (dokunulmaz)
```

`repo.toml` örneği:

```toml
id = "6f1c2c1e-…"
name = "Aile Arşivi"
schema_version = 1
created_at = "2026-11-05T10:00:00+03:00"

[analysis]
metadata = true
dedup = true
content = true          # CLIP etiket + anlamsal arama
faces = true
video_scenes = false
transcripts = false

[dedup]
keep_rule = "quality"            # quality | oldest | primary_folder
primary_folders = ["DCIM"]
near_threshold = 6               # pHash Hamming mesafesi (64 bit)

[scan]
auto_track = false               # yeni dosyalar "takipsiz" olarak bekler
```

- **Taşınabilirlik.** Tüm yollar repo köküne göre **göreli** ve **NFC** biçiminde saklanır. macOS dosya adlarını NFD verebiliyor; Türkçe karakterler için bu normalleştirme kritik.
- **Çöp ve dosya taşıma** aynı birim (volume) içinde `rename` ile yapılır. Bu yüzden anlıktır ve harici diskte de kopyalama gerektirmez.

### 5.2 Global (repo dışı)

```
~/Library/Application Support/MediaMind/
├─ repos.json      [{id, name, last_path, last_seen, volume_name}]  ← menüdeki liste
├─ settings.toml   tema, kısayollar, varsayılan model, asistan sağlayıcısı
└─ models/         indirilen ONNX modelleri (repolar arasında ortak, bir kez indirilir)
macOS Keychain     LLM API anahtarları
```

- Takılı olmayan disk menüde "çevrimdışı" olarak görünür.
- Disk farklı bir yola bağlanırsa uygulama `/Volumes/*/` altında aynı `id`'ye sahip `.mediamind` klasörünü arar ve yolu kendiliğinden günceller.

## 6. Veri modeli (SQLite)

Temel ayrım: **asset** içeriği temsil eder (hash ile tanımlanır), **file** ise bu içeriğin diskteki bir kopyasıdır. Aynı içeriğe sahip iki dosya aynı asset'e bağlanır. Bu sayede birebir tekrarlar doğal olarak ortaya çıkar ve taşınan bir dosya içerik hash'inden tanınır.

| Tablo | Önemli alanlar | Not |
|---|---|---|
| `assets` | id, blake3, size, kind (photo/video), mime, width, height, duration_ms, taken_at, taken_at_source, tz_offset, lat, lon, alt, place_id, camera_make, camera_model, lens, orientation, phash, dhash, quality_score, blur_score, rating, favorite, analysis_state (json: aşama → sürüm) | İçerik düzeyi |
| `files` | id, asset_id, rel_path (benzersiz, NFC), size, mtime_ns, inode, status (untracked/tracked/missing), first_seen, last_seen | Yol düzeyi |
| `sidecars` | file_id, rel_path, type (aae/xmp/live_mov) | Taşınırken dosyayla birlikte hareket eder |
| `asset_links` | a_id, b_id, type (live_photo/burst/edit/same_scene) | Live Photo eşleşmesi, seri çekimler |
| `places` | id, geoname_id, name, admin1, country | Offline reverse geocoding sonucu |
| `tags`, `asset_tags` | tag: id, name, kind (object/scene/animal/user/album) · asset_tag: source (model/user), confidence, model_ver | Model etiketi ile kullanıcı etiketi ayrı tutulur |
| `asset_vectors` | asset_id, model, vector | sqlite-vec sanal tablosu (CLIP embedding) |
| `faces` | id, asset_id, frame_ms (video için), bbox, quality, vector, cluster_id, person_id, assigned_by (auto/user) | |
| `persons` | id, name, kind (human/pet), cover_face_id, hidden | |
| `video_scenes` | asset_id, start_ms, end_ms, keyframe | |
| `transcripts` | asset_id, start_ms, end_ms, text | FTS5 ile aranır |
| `search_fts` | FTS5: dosya adı, etiketler, yer, kişiler, döküm | Metin araması |
| `dup_groups`, `dup_members` | group: kind (exact/near/burst/video), status · member: file_id, role (keep/delete/optional), reason | |
| `plans`, `plan_items` | plan: kind, source (ui/assistant/mcp:<istemci>), status (draft/pending/applied/rejected/undone/failed), summary · item: op (move/trash/restore/tag/merge), src, dst, status, error | Tüm değişikliklerin günlüğü |
| `jobs` | id, type, asset_id, priority, status, attempts, error | Analiz kuyruğu |
| `schema_migrations` | version, applied_at | Repo şeması sürümü |

## 7. Tarama ve durum tespiti

`mediamind status` komutunun yaptığı iş:

1. **Yürüyüş.** Repo kökü gezilir. `.mediamind/`, `.mediamindignore` kuralları ve sistem dosyaları (`.DS_Store`, `._*`, `.Spotlight-V100`, `.Trashes`) atlanır. Medya olup olmadığı uzantı ve imza ile belirlenir.
2. **Hızlı geçiş.** Yol DB'de varsa ve `size` ile `mtime_ns` aynıysa dosya değişmemiştir; okuma yapılmaz. Hedef: 50 bin dosyalık bir SSD reposunda bu geçiş 1 dakikanın altında bitmeli.
3. **Hash.** Yeni ya da değişmiş dosyaların BLAKE3 hash'i (mmap, çok iş parçacıklı) hesaplanır. Aşama 1'de her zaman tam hash alınıyor; büyük video arşivlerinde ölçüm sonucu yavaş kalırsa **hızlı parmak izi** (baş/orta/son 1'er MB) + arka planda tam hash eklenecek.
4. **Sınıflandırma:**

   | Durum | Koşul |
   |---|---|
   | **new (takipsiz)** | Yol bilinmiyor, içerik bilinmiyor |
   | **moved** | Yol bilinmiyor, içerik daha önce görülmüş bir dosyayla aynı ve o dosya artık yerinde yok |
   | **copy** | Yol bilinmiyor, içerik hâlâ yerinde duran bir dosyayla aynı (birebir tekrar) |
   | **modified** | Yol biliniyor, hash değişmiş |
   | **missing** | DB'de var, diskte yok |

5. **Takibe alma.** Yeni dosyalar `auto_track` kapalıysa "takipsiz" bekler. Kullanıcı "Takibe al" dediğinde ya da `mediamind track` çalıştığında analiz kuyruğuna girer.
6. **Canlı izleme.** Uygulama açıkken FSEvents (watchdog) ile değişiklikler artımlı olarak işlenir. Disk takıldığında hızlı geçiş kendiliğinden çalışır. ✅ (core/src/mediamind/watch.py: olaylar toplanır, klasör 3 sn sakinleşip dosya boyutları sabitlenince artımlı tarama; .mediamind yok sayılır; Ayarlar › Genel › "Klasördeki değişiklikleri izle")

Uç durumlar:
- exFAT diskte inode güvenilir değildir; yalnızca boyut, mtime ve hash kullanılır.
- Büyük/küçük harf duyarsız dosya sistemlerinde yeniden adlandırma kontrolü yapılır.
- iCloud'da duran ama indirilmemiş dosyalar (yer tutucu) okunmaz, ayrıca işaretlenir.

## 8. Analiz hattı

Analiz, öncelikli bir iş kuyruğunda aşama aşama ilerler. Her aşama hangi model sürümüyle çalıştığını `analysis_state` alanına yazar. Model değişirse yalnızca ilgili aşama yeniden çalışır.

| # | Aşama | Araç | Çıktı | Yaklaşık maliyet |
|---|---|---|---|---|
| 1 | Meta veri | **exiftool** (toplu `-json` modu), videoda **ffprobe** | tarih, GPS, kamera, lens, boyut, süre, codec | Çok hızlı |
| 2 | Tarih çıkarımı | EXIF DateTimeOriginal → QuickTime CreationDate → dosya adı kalıpları (`IMG-20240815-WA0012`, `2024-08-15 …`) → mtime | `taken_at`, `taken_at_source` | — |
| 3 | Konum | GeoNames `cities1000` + KD-ağacı (offline), Türkçe adlar için alternatif ad tablosu | şehir, il, ülke | Çok hızlı |
| 4 | Önizleme | Pillow + pillow-heif. RAW için gömülü JPEG (rawpy/exiftool). Video için ffmpeg kare yakalama. | s/m/l WebP | Hızlı |
| 5 | Algısal hash | imagehash (pHash, dHash) | 64 bit hash | Hızlı |
| 6 | İçerik | SigLIP2 base-224, int8 ONNX (çok dilli) · ONNX Runtime CPU · önizleme (m) görselinden | 768 boyutlu vektör + sıfır-atış etiketler (~65 etiketlik TR/EN sözlük, z-skoru eşiği) | ~30 ms/öğe |
| 7 | Kalite | Laplacian varyansı (bulanıklık), pozlama, göz açık/kapalı | `blur_score`, `quality_score` | Hızlı |
| 8 | Yüz | InsightFace buffalo_l (SCRFD 10g + ArcFace w600k) · ONNX Runtime, ön/son işleme numpy/OpenCV · önizleme (m) görselinden | yüz kutuları + 512 boyutlu imza + kalite skoru | ~55 ms/öğe |
| 9 | Yüz kümeleme | Adlı kişilere merkez benzerliği ≥ 0,50 ise otomatik atama; kalanlar ortalama bağlantılı hiyerarşik kümeleme (kosinüs ≥ 0,42). "Bu o değil" denen yüz aynı kişiye tekrar atanmaz. | kümeler, birleştirme önerileri (≥ 0,36) | Hafif |
| 10 | Video sahne | PySceneDetect / ffmpeg sahne filtresi. Anahtar karelerde aşama 6 ve 8 çalışır. | sahneler, kişi zaman çizelgesi | Ağır |
| 11 | Konuşma dökümü | whisper.cpp (Metal) ya da mlx-whisper | zaman damgalı metin | Ağır, varsayılan kapalı |

- **Öncelik.** Kullanıcının o an baktığı klasör ve tarih aralığı önce işlenir. Ağır aşamalar, makine güç kaynağına bağlıyken ve boştayken hızlanır.
- **Türkçe arama.** CLIP modelleri çoğunlukla İngilizce eğitilir. Bu yüzden çok dilli bir model seçilecek. Asistan açıksa sorgu ayrıca İngilizceye çevrilip iki sonuç birleştirilebilir.
- **Lisans.** InsightFace'in hazır modelleri ticari olmayan kullanım lisanslıdır; kişisel kullanımda sorun yok. Ürünleşme olursa açık lisanslı bir alternatife geçilmeli.

## 9. Tekrar tespiti

| Tür | Yöntem | Varsayılan davranış |
|---|---|---|
| Birebir aynı | Aynı BLAKE3 → aynı asset, birden çok file | Biri TUT, diğerleri SİL olarak önerilir |
| Neredeyse aynı | pHash Hamming ≤ 6, ardından CLIP kosinüs benzerliği ≥ 0,95 ile doğrulama. BK-ağacı ile aranır. | Biri TUT, diğerleri SİL olarak önerilir |
| Seri çekim / benzer | Çekimler arası ≤ 3 sn ve CLIP benzerliği ≥ 0,92 | En iyi kare önerilir, diğerleri **otomatik seçilmez** |
| Video kopyası | Süre farkı ±%1 ve anahtar karelerin pHash dizisi eşleşmesi | Biri TUT, diğerleri SİL olarak önerilir |

**Tutulacak dosyanın seçimi** (`keep_rule = quality`): sırasıyla şu ölçütlere bakılır, ilk farklılıkta karar verilir.
1. Çözünürlük (piksel sayısı)
2. Format kalitesi: RAW > HEIC/PNG > JPEG; videoda bit hızı
3. EXIF eksiksizliği: tarih ve GPS var mı
4. Mesajlaşma uygulaması klasöründe değil mi (WhatsApp, Telegram vb.)
5. `primary_folders` içinde mi
6. En eski `taken_at`, sonra en eski `first_seen`

Tekrar gruplarının onaylanması bir **silme planı** üretir (bkz. 10).

## 10. Planlar, uygulama ve geri alma

Dosya sistemini değiştiren her işlem bir plandan geçer. Arayüz, CLI, asistan ve MCP ayrımı yoktur.

1. **Oluştur.** `create_plan(kind, items)` çağrısı `draft` durumunda bir plan kaydeder. Prova bilgisi de hesaplanır: kaç dosya, ne kadar yer, çakışmalar.
2. **Onay.** Plan `pending` durumuna geçer. Arayüzde plan kartı görünür. Ajan izinleri izin veriyorsa plan doğrudan uygulanabilir. Taşıma ve silme için varsayılan, her zaman onay istemektir.
3. **Uygula.** Her öğe için önce durum `running` olarak yazılır, sonra işlem yapılır, ardından `done` ya da `failed` yazılır (önce-yaz günlüğü). Uygulama çökerse açılışta yarım kalan plan kaldığı yerden sürdürülür ya da geri alınır.
   - Silme işlemi dosyayı `.mediamind/trash/<plan_id>/<rel_path>` konumuna taşır (aynı birimde `rename`).
   - Yan dosyalar (`.AAE`, `.XMP`, Live Photo `.MOV`) asıl dosyayla birlikte taşınır.
   - Çakışan adlara `-1`, `-2` eklenir.
   - Hiçbir işlem repo kökünün dışına çıkamaz; yollar normalize edilip kontrol edilir.
4. **Geri al.** `undo(plan_id)` öğeleri ters sırayla geri çevirir. Hedefte o arada başka bir dosya oluşmuşsa öğe atlanır ve bildirilir.
5. **Çöpü boşalt.** Varsayılan saklama süresi 30 gündür. Gerçek silme yalnızca çöp boşaltılırken yapılır ve ayrıca onay ister.

## 11. Düzenleme şablonları

| Belirteç | Örnek | Değer yoksa |
|---|---|---|
| `{yıl}` `{ay}` `{gün}` | 2024 / 08 / 15 | `_tarihsiz` |
| `{ay_adı}` | Ağustos | `_tarihsiz` |
| `{şehir}` `{ülke}` | Bodrum / Türkiye | `_konumsuz` |
| `{kişi}` | Ayşe (en belirgin kişi) | `_kişisiz` |
| `{kamera}` | iPhone 15 Pro | `_bilinmeyen` |
| `{tür}` | Fotoğraf / Video | — |
| `{etiket}` | ilk kullanıcı etiketi | `_etiketsiz` |
| `{dosya}` | özgün dosya adı | — |

- Hazır şablonlar: Yıl/Ay, Yıl/Konum, Kişi/Yıl, Tür/Yıl/Ay.
- Kapsam üç seçenekli: tüm repo, yalnız takipsizler ya da seçili öğeler.
- Varsayılan işlem taşımadır; istenirse kopyalama seçilebilir.
- Dosya adlarındaki geçersiz karakterler temizlenir ve NFC'ye çevrilir.
- Sonuç bir **taşıma planıdır**. Prova görülür, onaylanır, geri alınabilir.

## 12. Arama

Arama üç katmanın birleşimidir:
1. **Yapısal filtreler:** tarih aralığı, tür, kişi, yer, etiket, kamera, puan, favori, durum (takipsiz/eksik).
2. **Metin araması (FTS5):** dosya adı, etiketler, yer adları, kişi adları, konuşma dökümü.
3. **Anlamsal arama:** sorgu metni CLIP ile vektöre çevrilir ve sqlite-vec ile en yakın komşular aranır ("kumsalda köpek").

Asistan açıksa doğal dil sorgusu önce yapısal filtrelere ayrıştırılır. "2023 yazı Ayşe ile" şuna dönüşür: tarih 2023-06..2023-08, kişi Ayşe. Kalan kısım anlamsal aramaya gider. Asistan kapalıyken basit kurallı bir ayrıştırıcı kullanılır.

## 13. Çekirdek API

Arayüz ile çekirdek arasında yerel HTTP kullanılır: `127.0.0.1`, rastgele port ve başlangıçta üretilen oturum token'ı. Uç noktaların bir kısmı:

| Uç nokta | İş |
|---|---|
| `GET /repos` · `POST /repos` · `DELETE /repos/{id}` | Repo listesi, klasörü repo yapma, menüden kaldırma |
| `GET /repos/{id}/status` · `POST …/scan` · `POST …/track` | Durum, tarama, takibe alma |
| `GET …/assets?filter…&cursor` | Sayfalı listeleme (sanal ızgara için) |
| `GET …/assets/{aid}` | Ayrıntı |
| `GET …/media/{aid}?size=s\|m\|l\|orig` | Önizleme ya da orijinal. Video için HTTP Range desteği var. |
| `GET …/search?q=` | Hibrit arama |
| `GET …/people` · `POST …/people/{pid}/merge` · `POST …/clusters/{cid}/name` | Kişi işlemleri |
| `GET …/dups?kind=` | Tekrar grupları |
| `POST …/organize/preview` | Şablon provası |
| `POST …/plans` · `POST …/plans/{id}/apply` · `POST …/plans/{id}/undo` | Planlar |
| `WS /events` | Tarama ve analiz ilerlemesi, yeni plan, dosya değişikliği olayları |

## 14. MCP sunucusu

- **Aktarım:** `stdio` (`mediamind mcp`, Claude Desktop ve Claude Code için) ve yerel HTTP (`/mcp`).
- **Çoklu repo:** Araçlar `repo` parametresi alır (ad ya da id). `list_repos` ile keşfedilir.

| Araç | Tür | Açıklama |
|---|---|---|
| `list_repos` | okuma | Repolar, durumları, öğe sayıları |
| `repo_status` | okuma | Yeni / taşınmış / eksik özeti |
| `search_media` | okuma | Filtreler + serbest metin, sayfalı sonuç |
| `get_media` | okuma | Meta veri, etiketler, kişiler. Önizleme yalnızca izin varsa döner. |
| `list_people` · `list_tags` · `list_places` | okuma | Sözlükler |
| `find_duplicates` | okuma | Gruplar ve tut/sil önerileri |
| `preview_layout` | okuma | Şablon provası |
| `tag_media` | yazma (hafif) | Etiket ekle/çıkar. Doğrudan uygulanır ama günlüğe yazılır. |
| `create_plan` | yazma | Taşıma, silme, birleştirme, düzenleme planı üretir. Uygulamaz. |
| `apply_plan` | yazma | Planı uygular. İzin ayarı "onayımı iste" ise kullanıcı onayı gelene kadar bekler ya da reddedilir. |
| `undo_plan` | yazma | Geri alır |

**Gizlilik:** Varsayılan olarak ajana yalnızca meta veri ve etiketler gider. Görsel içeriğin (önizleme) gönderilmesi ayrı bir izindir ve varsayılan olarak kapalıdır.

## 15. Yerleşik asistan

- Sağlayıcı soyutlaması üzerine kurulur. Varsayılan Anthropic, model Claude Opus 5.5 (ayarlardan Sonnet 5.5 / Haiku 5.5 seçilebilir), düşünme düzeyi `medium`; reddedilen isteklerde sunucu tarafı yedek model (`fallbacks: "default"`). OpenRouter ve Google Gemini de desteklenir (OpenAI uyumlu uçlar üzerinden; modeller sağlayıcının listesinden canlı seçilir, araç çağırmayı destekleyenler gösterilir). Sağlayıcı başına ayrı API anahtarı Anahtar Zinciri'nde tutulur. Sağlayıcı ya da model değişince yeni konuşma başlar. İleride yerel (Ollama) eklenebilir.
- MCP ile aynı araç setini süreç içinden çağırır. Böylece iki kanal birebir aynı yetkilere sahip olur.
- Sayfa bağlamını alır: açık sayfa, seçili öğeler, aktif filtre.
- API anahtarı macOS Keychain'de saklanır, hiçbir zaman repoya yazılmaz.

## 16. Arayüz

- **Teknoloji:** Tauri 2, React, TypeScript, Vite. Veri katmanı TanStack Query. Izgara TanStack Virtual ile sanallaştırılır (100 bin öğede akıcı kaydırma).
- **Tema:** Renkler CSS değişkenleriyle tanımlanır (token). Açık ve koyu tema aynı token'lardan üretilir. Varsayılan sistem temasını izler, ayarlardan elle değiştirilebilir. Görüntüleyici ve oynatıcı iki temada da koyu zeminde kalır.
- **Görüntüleyici:** Ekrandaki karenin iki önü ve iki arkası önceden yüklenir (hedef: geçiş < 100 ms). Önce `m` boyutu gösterilir, ardından `l` boyutu gelir. %100 yakınlaştırmada orijinal yüklenir. HEIC ve RAW dosyaları gömülü önizlemeyle anında açılır.
- **Video:** HTML5 `<video>` kullanılır; WebKit H.264 ve HEVC'yi doğrudan oynatır. Desteklenmeyen formatlarda önizleme kopyası gösterilir. Sahneler, kişi zaman çizelgesi ve döküm aynı zaman çubuğuna bağlıdır.
- **Kısayollar:**

  | Tuş | İşlev |
  |---|---|
  | ⌘K | Arama |
  | ⌘J | Asistan |
  | ← → | Gezin |
  | Boşluk | Slayt gösterisi |
  | Z | Yakınlaştır |
  | F | Yüzler |
  | I | Bilgi paneli |
  | 1-5 | Puan |
  | Del | Çöpe at |
  | Esc | Kapat |

## 17. Aşamalar

Her aşama kendi başına kullanılabilir bir sonuç verir.

### Aşama 1 — Çekirdek ve CLI ✅ (2026-11-05)
- Repo: `init`, `.mediamind` yapısı, `repo.toml`, kilit, global `repos.json`
- Tarama: `status`, `scan`, `track`, ignore kuralları, durum tespiti (new/moved/copy/modified/missing)
- Meta veri: exiftool/ffprobe, tarih çıkarımı, offline geocode
- Önizleme üretimi (s/m/l), pHash
- Tekrarlar: birebir, neredeyse aynı ve video. `dups` komutu ve tut kuralı.
- Planlar: oluştur, prova, uygula, geri al, çöp. `organize` şablon motoru.
- **Kabul ölçütü:** Gerçek bir harici disk klasöründe `init → scan → track → dups → plan → apply → undo` döngüsü veri kaybı olmadan çalışır. Testler bu döngüyü kapsar.

### Aşama 2 — Masaüstü arayüz ✅ (2026-11-05)
- Tauri kabuğu, servis yaşam döngüsü, klasör seçici, repo menüsü
- Kütüphane (sanal ızgara, filtreler, tıklayınca açılan detay paneli), görüntüleyici, video oynatıcı
- Tekrarlar ve Düzenle ekranları, işlem geçmişi, açık/koyu tema
- **Kabul ölçütü:** 50 bin öğelik bir repoda akıcı kaydırma; görüntüleyicide oklarla geçişte bekleme hissedilmez.

### Aşama 3 — MCP ve asistan ✅ (2026-11-05)
- `mediamind mcp` (stdio + HTTP), araç seti, izin katmanı
- Yerleşik asistan paneli (her sayfada), Keychain, plan kartları
- **Kabul ölçütü:** Claude Desktop'tan "Bodrum 2024 videolarını şu klasöre taşı" dendiğinde uygulamada onay bekleyen bir plan belirir, onaylanınca uygulanır.

### Aşama 4 — Akıllı analiz ✅ (2026-11-05)
- Model indirme yöneticisi, iş kuyruğu öncelikleri
- CLIP etiketleme ve anlamsal arama, kalite skorları, seri çekim tespiti
- Yüz tespiti ve kümeleme, Kişiler ekranı, ad verme ve birleştirme önerileri

### Aşama 5 — Video derinliği ✅ (2026-11-05)
- Sahne tespiti, anahtar kare analizi, kişi zaman çizelgesi
- Konuşma dökümü (isteğe bağlı), dökümde arama, klip çıkarma ve kareyi fotoğraf yapma

## 18. Geliştirme ortamı

| Gereksinim | Durum (2026-11-05) | Kurulum |
|---|---|---|
| Python 3.12 | Sistemde 3.9.6 var; projede uv ile 3.12 kullanılacak | `uv python install 3.12` |
| uv | Kurulu | — |
| Node.js | Kurulu | — |
| Rust (Tauri için) | **Yok** | `rustup` (Aşama 2'de gerekli) |
| ffmpeg / ffprobe | **Yok** | `brew install ffmpeg` |
| exiftool | **Yok** | `brew install exiftool` |

Dağıtım aşamasında ffmpeg, exiftool ve Python çalışma zamanı uygulama paketinin içine gömülecek. Kullanıcının ayrıca bir şey kurması gerekmeyecek.

## 19. Riskler ve açık konular

- **Apple Photos kütüphanesi** (`.photoslibrary`) kapsam dışı. Bu paketler ignore edilir; ileride salt-okuma içe aktarma düşünülebilir.
- **Harici diskin uykuya geçmesi ya da çıkarılması:** taramalar ve plan uygulaması kesintiye dayanıklı olmalı. Önce-yaz günlüğü ve kaldığı yerden sürdürme bu yüzden var.
- **Büyük video arşivlerinde tam hash süresi:** hızlı parmak izi + arka plan tam hash ile çözülüyor; gerçek veriyle ölçülmeli.
- **Model boyutları:** CLIP yaklaşık 300–900 MB, yüz modelleri yaklaşık 300 MB, whisper 150 MB–1,5 GB. İlk kurulumda isteğe bağlı indirme yapılacak.
- **Türkçe sorgu kalitesi:** Çok dilli model seçimi küçük bir karşılaştırma testiyle (örnek sorgular üzerinde) yapılacak.

## Ek: Aşama 4 uygulama notları (2026-11-05)

- Modeller `<MEDIAMIND_HOME>/models/` altında, sabit commit'e kilitli Hugging Face depolarından iner
  (`onnx-community/siglip2-base-patch16-224-ONNX`, `immich-app/buffalo_l`); toplam ~576 MB. Ayarlar › Akıllı analiz'den indirilir.
- Ölçüm (Apple Silicon, 2.000 öğe): içerik + yüz analizi ~12 öğe/sn → 50 bin öğe ≈ 70 dk.
- Anlamsal arama eşiği: kosinüs ≥ 0,075 ve en iyi sonuca ≤ 0,05 uzaklık (karşılığı olmayan sorgular boş döner).
- Seri çekim: ≤ 3 sn arayla, aynı kamera, görsel benzerlik ≥ 0,90; en net kare "tut", diğerleri "isteğe bağlı" (otomatik silme planına girmez).
- CoreML yürütücüsü int8 modellerde hızlanma sağlamadığı için CPU kullanılıyor; fp16 + CoreML denemesi ileride.

## Ek: Aşama 5 uygulama notları (2026-11-05)

- Oynatma: WebKit'in oynatabildiği kapsayıcı/codec'ler (MP4/MOV/M4V + H.264/HEVC/ProRes) doğrudan; diğerleri için ilk açılışta
  `.mediamind/proxies/` altına H.264/AAC önizleme kopyası üretilir (orijinale dokunulmaz).
- Video analizi: yalnız anahtar kareler çözülür, aralarından eşit aralıklı en fazla 150 kare seçilir. Kare vektörlerinden sahneler
  (ardışık benzerlik < 0,82 ya da 60 sn), video vektörü (ortalama) ve etiketler; her karede yüz → kişi zaman çizelgesi; video içi anlamsal arama.
- Konuşma dökümü: faster-whisper small (int8, CPU), ffmpeg ile 16 kHz WAV; repo ayarıyla açılır (varsayılan kapalı). Döküm metin aramasına dahildir.
- Kareyi fotoğraf yap ve klip çıkar: yeni dosya kaynağın yanına yazılır, tarih (kareye denk gelen an) ve konum taşınır, takibe alınır.
- İş şeritleri: analiz uzun sürse de tarama, plan ve önizleme kopyası işleri bekletilmez; analiz repo kilidini yalnız kısa yazımlarda tutar.
- OpenCV bağımlılığı kaldırıldı (yüz ön/son işleme numpy/Pillow); faster-whisper'ın PyAV'ı ile aynı süreçte çakışan ffmpeg kütüphaneleri yüklenmiyor.


## Kütüphane özellikleri (2026-11-05)

- **Yazıyla arama (OCR):** macOS Vision (yerel), akıllı analizin `ocr` aşaması → `asset_text`; arama kutusu ve asistan kullanır.
- **Temizlik önerileri:** ekran görüntüsü (dosya adı ya da model + kamera bilgisi yok), kazara (karanlık/tek renk: `luma`/`contrast`, `quality` aşaması), bulanık (`blur_score`), çok kısa ve çok büyük videolar. Favori/puanlı/albümdekiler önerilmez; “kalsın” → `cleanup_keep`.
- **Harita:** Leaflet + OpenStreetMap döşemeleri (yalnız harita alanı istenir), küçük resimli kümeler; konum atamada seçme kipi.
- **Türkçe yer adları:** `data/place_names.tsv.gz` (GeoNames asıl yazımlar, `scripts/build_place_names.py`) + il eşlemesi; `places.name_fold` ile aksansız arama.
- **Anılar:** geçmiş yıllarda bugün (±3 gün), geziler (evden uzakta art arda günler; ev = en çok gün geçirilen yer), kalabalık günler.
- **Albümler:** elle ve akıllı (kayıtlı süzgeç) albümler; `assets?album=`.
- **Dışa aktarma:** küçük/büyük/orijinal, isteğe bağlı GPS silme (exiftool), tarih bilgisi korunur.
- **Tarih/konum düzeltme:** yalnız veritabanında (`asset_edits`), yeniden analizde korunur, geri alınabilir.
- **Son silinenler:** isteğe bağlı N gün sonra kendiliğinden kalıcı silme; kalıcı silmede önizleme/yüz/kare/kopya dosyaları da temizlenir.
