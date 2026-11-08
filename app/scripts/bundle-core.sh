#!/bin/bash
# MediaMind.app'i tek parça (kendi başına çalışan) hale getirir:
#   Contents/Resources/core/python        Python çalışma ortamı (uv'nin taşınabilir CPython'u)
#   Contents/Resources/core/site-packages  çekirdek ve bağımlılıkları
#   Contents/Resources/core/bin           mediamind başlatıcısı, ffmpeg/ffprobe, exiftool
#
# Kullanım:
#   scripts/bundle-core.sh [MediaMind.app yolu] [hedef_mimari: x86_64|aarch64]
#   veya:
#   scripts/bundle-core.sh --app <yol> --arch <x86_64|aarch64>

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CORE="$REPO/core"

APP=""
TARGET_ARCH=""

# Parametre ayrıştırma
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)
      APP="$2"; shift 2 ;;
    --arch)
      TARGET_ARCH="$2"; shift 2 ;;
    *)
      if [ -z "$APP" ]; then
        APP="$1"
      elif [ -z "$TARGET_ARCH" ]; then
        TARGET_ARCH="$1"
      fi
      shift ;;
  esac
done

# Varsayılan uygulama yolu
if [ -z "$APP" ]; then
  if [ -d "$REPO/app/src-tauri/target/x86_64-apple-darwin/release/bundle/macos/MediaMind.app" ] && [ "$TARGET_ARCH" = "x86_64" ]; then
    APP="$REPO/app/src-tauri/target/x86_64-apple-darwin/release/bundle/macos/MediaMind.app"
  elif [ -d "$REPO/app/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/MediaMind.app" ] && [ "$TARGET_ARCH" = "aarch64" ]; then
    APP="$REPO/app/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/MediaMind.app"
  else
    APP="$REPO/app/src-tauri/target/release/bundle/macos/MediaMind.app"
  fi
fi

if [ ! -d "$APP" ]; then
  echo "Hata: Belirtilen uygulama paketi bulunamadı: $APP" >&2
  exit 1
fi

BIN="$APP/Contents/MacOS/mediamind-app"
if [ ! -f "$BIN" ]; then
  echo "Hata: Uygulama ikilisi bulunamadı: $BIN" >&2
  exit 1
fi

# Hedef mimariyi ikiliden otomatik tespit et (belirtilmemişse)
if [ -z "$TARGET_ARCH" ]; then
  FILE_INFO="$(file "$BIN")"
  if echo "$FILE_INFO" | grep -q "x86_64"; then
    TARGET_ARCH="x86_64"
  elif echo "$FILE_INFO" | grep -q "arm64"; then
    TARGET_ARCH="aarch64"
  else
    TARGET_ARCH="$(uname -m)"
    [ "$TARGET_ARCH" = "arm64" ] && TARGET_ARCH="aarch64"
  fi
fi

echo "=========================================================="
echo " MediaMind Core Paketleme"
echo " Hedef Mimari : $TARGET_ARCH"
echo " Paket Yolu    : $APP"
echo "=========================================================="

R="$APP/Contents/Resources/core"
rm -rf "$R"; mkdir -p "$R/bin" "$R/media/bin" "$R/media/lib"

# 1) Python Çalışma Ortamı (Taşınabilir CPython)
echo "→ 1/6 Python ($TARGET_ARCH) hazırlanıyor..."
PY_REQUEST="cpython-3.12-macos-${TARGET_ARCH}-none"

PY_SRC=""
if command -v uv >/dev/null 2>&1; then
  PY_SRC="$(uv python find "$PY_REQUEST" 2>/dev/null || true)"
  if [ -z "$PY_SRC" ]; then
    echo "  $PY_REQUEST uv önbelleğinde bulunamadı, indiriliyor..."
    uv python install "$PY_REQUEST" || true
    PY_SRC="$(uv python find "$PY_REQUEST" 2>/dev/null || true)"
  fi
fi

# uv bulamadıysa veya yerel geliştirme ortamında aynı mimariysek
if [ -z "$PY_SRC" ] && [ "$TARGET_ARCH" = "$(uname -m | sed 's/arm64/aarch64/')" ] && [ -x "$CORE/.venv/bin/python" ]; then
  PY_SRC="$CORE/.venv/bin/python"
fi

if [ -z "$PY_SRC" ] || [ ! -x "$PY_SRC" ]; then
  echo "Hata: $TARGET_ARCH mimarisi için Python bulunamadı!" >&2
  echo "Lütfen 'uv python install $PY_REQUEST' komutunu çalıştırın." >&2
  exit 1
fi

PY_HOME="$("$PY_SRC" -c 'import sys; print(sys.base_prefix)' 2>/dev/null || dirname "$(dirname "$PY_SRC")")"
echo "  Kaynak: $PY_HOME"
rsync -a --exclude 'lib/python3.*/test' --exclude 'lib/python3.*/idlelib' --exclude 'lib/python3.*/tkinter' \
  --exclude 'lib/python3.*/turtledemo' --exclude 'lib/tcl*' --exclude 'lib/tk*' --exclude 'lib/itcl*' --exclude '__pycache__' \
  "$PY_HOME/" "$R/python/"
PY="$R/python/bin/python3"

# 2) Bağımlılıklar (uv.lock / pyproject.toml gereksinimleri)
echo "→ 2/6 Python paketleri kuruluyor ($TARGET_ARCH)..."
REQ="$(mktemp)"
(cd "$CORE" && uv export --no-dev --no-hashes --format requirements-txt --no-emit-project > "$REQ")
uv pip install --python "$PY" --target "$R/site-packages" -r "$REQ" --quiet
uv pip install --python "$PY" --target "$R/site-packages" --no-deps "$CORE" --quiet --reinstall-package mediamind
rm -f "$REQ"
find "$R/site-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$R/site-packages/bin"

# 3) ffmpeg / ffprobe
echo "→ 3/6 ffmpeg & ffprobe ($TARGET_ARCH) paketleniyor..."
FFMPEG_SRC=""
FFPROBE_SRC=""

if [ "$TARGET_ARCH" = "x86_64" ]; then
  CACHE_BIN="$REPO/core/.cache/bin/x86_64"
  if [ -x "$CACHE_BIN/ffmpeg" ] && [ -x "$CACHE_BIN/ffprobe" ]; then
    FFMPEG_SRC="$CACHE_BIN/ffmpeg"
    FFPROBE_SRC="$CACHE_BIN/ffprobe"
  elif [ -x "/usr/local/bin/ffmpeg" ] && file "/usr/local/bin/ffmpeg" | grep -q "x86_64"; then
    FFMPEG_SRC="/usr/local/bin/ffmpeg"
    FFPROBE_SRC="/usr/local/bin/ffprobe"
  else
    echo "  Intel (x86_64) statik ffmpeg indiriliyor (evermeet.cx)..."
    mkdir -p "$CACHE_BIN"
    TMP_DL="$(mktemp -d)"

    # evermeet API'sinden en güncel release zip URL'lerini al (veya doğrudan 9.x release'e yönel)
    FF_URL="$(curl -fsSL "https://evermeet.cx/ffmpeg/info" 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(next(item['download']['zip']['url'] for item in data if item.get('name')=='ffmpeg' and item.get('type')=='release'))" 2>/dev/null || echo "https://evermeet.cx/ffmpeg/ffmpeg-9.0.2.zip")"
    PR_URL="$(curl -fsSL "https://evermeet.cx/ffmpeg/info" 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(next(item['download']['zip']['url'] for item in data if item.get('name')=='ffprobe' and item.get('type')=='release'))" 2>/dev/null || echo "https://evermeet.cx/ffmpeg/ffprobe-9.0.2.zip")"

    curl -fsSL "$FF_URL" -o "$TMP_DL/ffmpeg.zip"
    unzip -q -o "$TMP_DL/ffmpeg.zip" -d "$TMP_DL"
    mv "$TMP_DL/ffmpeg" "$CACHE_BIN/ffmpeg"

    curl -fsSL "$PR_URL" -o "$TMP_DL/ffprobe.zip"
    unzip -q -o "$TMP_DL/ffprobe.zip" -d "$TMP_DL"
    mv "$TMP_DL/ffprobe" "$CACHE_BIN/ffprobe"

    chmod +x "$CACHE_BIN/ffmpeg" "$CACHE_BIN/ffprobe"
    rm -rf "$TMP_DL"
    FFMPEG_SRC="$CACHE_BIN/ffmpeg"
    FFPROBE_SRC="$CACHE_BIN/ffprobe"
  fi

  # Statik ikilileri kopyala ve karantinayı kaldır
  cp "$FFMPEG_SRC" "$R/media/bin/ffmpeg"
  cp "$FFPROBE_SRC" "$R/media/bin/ffprobe"
  chmod 755 "$R/media/bin/ffmpeg" "$R/media/bin/ffprobe"
  xattr -dr com.apple.quarantine "$R/media/bin" 2>/dev/null || true
  codesign --force --sign - "$R/media/bin/ffmpeg" >/dev/null 2>&1 || true
  codesign --force --sign - "$R/media/bin/ffprobe" >/dev/null 2>&1 || true
else
  # Apple Silicon (aarch64) için Homebrew dylib bağımlılıklarıyla bundle et
  python3 "$HERE/bundle_dylibs.py" "$R/media" ffmpeg ffprobe
fi

ln -sf ../media/bin/ffmpeg "$R/bin/ffmpeg"
ln -sf ../media/bin/ffprobe "$R/bin/ffprobe"

# 4) Exiftool (saf Perl; macOS'un evrensel perl'üyle çalışır)
echo "→ 4/6 ExifTool paketleniyor..."
EXIF_BIN="$(command -v exiftool || true)"
if [ -n "$EXIF_BIN" ]; then
  EXIF="$(cd "$(dirname "$(readlink -f "$EXIF_BIN")")/.." && pwd)"
  EXIF_LIBEXEC="$(sed -n 's/.*exec "\(.*\)\/bin\/exiftool".*/\1/p' "$EXIF_BIN")"
  [ -d "$EXIF_LIBEXEC" ] || EXIF_LIBEXEC="$EXIF/libexec"
  rsync -a --exclude man "$EXIF_LIBEXEC/" "$R/exiftool/"
  cat > "$R/bin/exiftool" <<'SH'
#!/bin/sh
D="$(cd "$(dirname "$0")/.." && pwd)"
PERL5LIB="$D/exiftool/lib/perl5" exec /usr/bin/perl "$D/exiftool/bin/exiftool" "$@"
SH
  chmod +x "$R/bin/exiftool"
else
  echo "  Uyarı: exiftool sistemde bulunamadı (brew install exiftool)" >&2
fi

# 5) Başlatıcı (launcher)
echo "→ 5/6 Başlatıcı komut dosyası oluşturuluyor..."
cat > "$R/bin/mediamind" <<'SH'
#!/bin/sh
D="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$D/site-packages" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PATH="$D/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export MEDIAMIND_LAUNCHER="$D/bin/mediamind"
exec "$D/python/bin/python3" -s -m mediamind.cli "$@"
SH
chmod +x "$R/bin/mediamind"

# 6) Ad-hoc İmzalama ve Doğrulama
echo "→ 6/6 İmzalama ve mimari doğrulaması yapılıyor..."
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || codesign --force --deep --sign - "$APP"

echo "----------------------------------------------------------"
echo " Mimari Doğrulama Özeti:"
echo "   Uygulama İkilisi : $(file "$BIN" | cut -d: -f2-)"
echo "   Python İkilisi   : $(file "$PY" | cut -d: -f2-)"
echo "   FFmpeg İkilisi   : $(file "$R/media/bin/ffmpeg" | cut -d: -f2-)"
echo "   Toplam Boyut     : $(du -sh "$R" | awk '{print $1}')"
echo "----------------------------------------------------------"
echo "✓ $TARGET_ARCH mimarisine özel tek parça uygulama hazır:"
echo "  $APP"
