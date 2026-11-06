#!/bin/bash
# MediaMind.app'i tek parça (kendi başına çalışan) hale getirir:
#   Contents/Resources/core/python       Python çalışma ortamı (uv'nin taşınabilir CPython'u)
#   Contents/Resources/core/site-packages çekirdek ve bağımlılıkları (geliştirme araçları hariç)
#   Contents/Resources/core/bin          mediamind başlatıcısı, ffmpeg/ffprobe (dylib'leriyle), exiftool
# Kullanım: scripts/bundle-core.sh [MediaMind.app yolu]   (varsayılan: tauri build çıktısı)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
APP="${1:-$REPO/app/src-tauri/target/release/bundle/macos/MediaMind.app}"
R="$APP/Contents/Resources/core"
CORE="$REPO/core"
echo "→ $R"
rm -rf "$R"; mkdir -p "$R/bin"

# 1) Python (taşınabilir build; test ve arayüz kitaplıkları atlanır)
PY_HOME="$("$CORE/.venv/bin/python" -c 'import sys; print(sys.base_prefix)')"
rsync -a --exclude 'lib/python3.*/test' --exclude 'lib/python3.*/idlelib' --exclude 'lib/python3.*/tkinter' \
  --exclude 'lib/python3.*/turtledemo' --exclude 'lib/tcl*' --exclude 'lib/tk*' --exclude 'lib/itcl*' --exclude '__pycache__' \
  "$PY_HOME/" "$R/python/"
PY="$R/python/bin/python3"

# 2) Bağımlılıklar (uv.lock'taki sürümler, önbellekten) + çekirdeğin kendisi
REQ="$(mktemp)"
(cd "$CORE" && uv export --no-dev --no-hashes --format requirements-txt --no-emit-project > "$REQ")
uv pip install --python "$PY" --target "$R/site-packages" -r "$REQ" --quiet
uv pip install --python "$PY" --target "$R/site-packages" --no-deps "$CORE" --quiet --reinstall-package mediamind
rm -f "$REQ"
find "$R/site-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$R/site-packages/bin"

# 3) ffmpeg / ffprobe (Homebrew kurulumundan, bağımlı kütüphaneleriyle)
python3 "$HERE/bundle_dylibs.py" "$R/media" ffmpeg ffprobe
ln -s ../media/bin/ffmpeg "$R/bin/ffmpeg"
ln -s ../media/bin/ffprobe "$R/bin/ffprobe"

# 4) exiftool (saf Perl; macOS'un kendi perl'üyle çalışır)
EXIF="$(cd "$(dirname "$(readlink -f "$(command -v exiftool)")")/.." && pwd)"
EXIF_LIBEXEC="$(sed -n 's/.*exec "\(.*\)\/bin\/exiftool".*/\1/p' "$(command -v exiftool)")"
[ -d "$EXIF_LIBEXEC" ] || EXIF_LIBEXEC="$EXIF/libexec"
rsync -a --exclude man "$EXIF_LIBEXEC/" "$R/exiftool/"
cat > "$R/bin/exiftool" <<'SH'
#!/bin/sh
D="$(cd "$(dirname "$0")/.." && pwd)"
PERL5LIB="$D/exiftool/lib/perl5" exec /usr/bin/perl "$D/exiftool/bin/exiftool" "$@"
SH
chmod +x "$R/bin/exiftool"

# 5) Başlatıcı (uygulama, Claude Desktop/Code MCP bağlantısı ve komut satırı için ortak)
cat > "$R/bin/mediamind" <<'SH'
#!/bin/sh
D="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$D/site-packages" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PATH="$D/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export MEDIAMIND_LAUNCHER="$D/bin/mediamind"
exec "$D/python/bin/python3" -s -m mediamind.cli "$@"
SH
chmod +x "$R/bin/mediamind"

# 6) İmza (ad-hoc; Apple geliştirici kimliği olmadan)
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || codesign --force --deep --sign - "$APP"
du -sh "$R" | sed 's/^/  boyut: /'
echo "✓ Tek parça uygulama hazır: $APP"
