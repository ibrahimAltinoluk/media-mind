# mediamind (çekirdek)

MediaMind'in Python çekirdeği ve komut satırı aracı. Tasarım: [../docs/PLAN.md](../docs/PLAN.md).

## Kurulum (geliştirme)

```sh
brew install exiftool ffmpeg
cd core
uv sync
uv run mediamind --help
uv run pytest
```

## Kullanım

```sh
cd /Volumes/T7/Aile
mediamind init --name "Aile Arşivi"   # .mediamind/ oluşturur, dosyalara dokunmaz
mediamind status                      # tarar: takipsiz / taşınmış / kopya / değişmiş / eksik
mediamind track [klasör…]             # takibe al + analiz (EXIF, tarih, konum, önizleme, pHash)
mediamind dups --plan                 # tekrarları bul, silinecekler için çöp planı oluştur
mediamind organize yil-ay             # şablonla taşıma planı (hazırlar: yil-ay, yil, yil-konum, tur-yil-ay, kamera-yil)
mediamind organize "{yıl}/{şehir}/{dosya}" --base Arsiv
mediamind plan list | show N | apply N | undo N | reject N
mediamind trash list | empty [--older-than 30]
mediamind repos list | add <yol> | remove <ad>
```

Dosya sistemini değiştiren her şey bir **plan** üzerinden gider: önce prova gösterilir, `plan apply` ile uygulanır,
`plan undo` ile geri alınır. Silinenler `.mediamind/trash/<plan>/` altına taşınır; kalıcı silme yalnızca `trash empty` ile.

`--repo/-r` ile başka bir klasördeki ya da kayıtlı adıyla bir repo seçilebilir (`mediamind status -r "Aile Arşivi"`).

## HTTP API

```sh
MEDIAMIND_TOKEN=gizli mediamind serve --port 7341
```

Masaüstü uygulaması bu API'yi kullanır. Yalnızca `127.0.0.1` üzerinde dinler; her istek `x-mm-token` başlığı
(ya da medya URL'lerinde `?t=`) ile token göndermelidir. Uç noktalar: `core/src/mediamind/api/server.py`.

## MCP sunucusu

```sh
mediamind mcp     # stdio
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "mediamind": { "command": "/…/media-mind/core/.venv/bin/mediamind", "args": ["mcp"] } } }
```

Claude Code: `claude mcp add mediamind -- /…/media-mind/core/.venv/bin/mediamind mcp`

Araçlar: `list_repos`, `repo_status`, `search_media`, `get_media`, `list_facets`, `find_duplicates`,
`preview_organize`, `update_media`, `create_plan`, `apply_plan`, `undo_plan`, `list_plans`, `get_preview_image`.
Taşıma/silme her zaman plan olarak oluşturulur; varsayılan ayarda `apply_plan` uygulamaz, kullanıcı MediaMind'da onaylar.
İzinler ve yerleşik asistan ayarları `<MEDIAMIND_HOME>/settings.json`; API anahtarı macOS Anahtar Zinciri'nde.
