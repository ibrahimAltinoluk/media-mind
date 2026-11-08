#!/bin/bash
# MediaMind Çoklu Mimari Build ve Paket Yönetim Scripti
# 2012+ Intel Mac'ler (x86_64, macOS 10.15 Catalina+) ve Apple Silicon (arm64, M1-M4) için.
#
# Kullanım:
#   ./scripts/build.sh intel       # 2012 Intel Mac için build alır ve paketler
#   ./scripts/build.sh arm64       # Apple Silicon Mac için build alır ve paketler
#   ./scripts/build.sh all         # Her iki mimari için sırayla build alır
#   ./scripts/build.sh list        # Mevcut build'leri listeler ve doğrular
#   ./scripts/build.sh clean       # Dağıtım ve derleme çıktılarını temizler

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/app"
CORE_DIR="$ROOT/core"
DIST_DIR="$ROOT/dist-releases"

# Renkler
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Cargo ve Rust ortamı
if [ -d "$HOME/.cargo/bin" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi
if [ -d "$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin" ]; then
  export PATH="$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin:$PATH"
fi

VERSION="$(grep '"version"' "$APP_DIR/package.json" | head -1 | sed -E 's/.*"version": *"([^"]+)".*/\1/')"

log_info() { echo -e "${BLUE}==>${NC} ${1}"; }
log_success() { echo -e "${GREEN}✓${NC} ${1}"; }
log_warn() { echo -e "${YELLOW}!${NC} ${1}"; }
log_error() { echo -e "${RED}✗${NC} ${1}"; }

check_prerequisites() {
  local target="$1"
  if ! command -v uv >/dev/null 2>&1; then
    log_error "'uv' bulunamadı! Lütfen 'brew install uv' ile yükleyin."
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    log_error "'npm' bulunamadı! Lütfen Node.js kurun."
    exit 1
  fi
  if ! command -v cargo >/dev/null 2>&1 && ! command -v rustup >/dev/null 2>&1; then
    log_error "Rust / Cargo bulunamadı! Lütfen rustup kurun."
    exit 1
  fi

  if [ "$target" = "x86_64" ]; then
    if ! rustup target list | grep -q "x86_64-apple-darwin (installed)"; then
      log_warn "Rust x86_64-apple-darwin hedefi eksik. Ekleniyor..."
      rustup target add x86_64-apple-darwin
    fi
  elif [ "$target" = "aarch64" ]; then
    if ! rustup target list | grep -q "aarch64-apple-darwin (installed)"; then
      log_warn "Rust aarch64-apple-darwin hedefi eksik. Ekleniyor..."
      rustup target add aarch64-apple-darwin
    fi
  fi
}

build_architecture() {
  local arch="$1" # "x86_64" veya "aarch64"
  local rust_target="${arch}-apple-darwin"
  local target_name=""
  
  if [ "$arch" = "x86_64" ]; then
    target_name="intel-x86_64"
    # 2012 Mac'ler için macOS 10.15 Catalina hedefi (Tauri 2 gereksinimi)
    export MACOSX_DEPLOYMENT_TARGET="10.15"
  else
    target_name="apple-silicon-arm64"
    export MACOSX_DEPLOYMENT_TARGET="11.0"
  fi

  echo ""
  log_info "=========================================================="
  log_info " BUILD BAŞLATILIYOR: $target_name (v$VERSION)"
  log_info " Hedef Mimari        : $arch ($rust_target)"
  log_info " Minimum macOS Sürümü : $MACOSX_DEPLOYMENT_TARGET"
  log_info "=========================================================="

  check_prerequisites "$arch"

  # 1. Frontend ve Tauri masaüstü kabuğunu derle
  log_info "1/3 Frontend ve Tauri ($rust_target) derleniyor..."
  (
    cd "$APP_DIR"
    # Vite ve frontend derlemesi
    npm run build
    # Tauri .app derlemesi
    npx tauri build --target "$rust_target"
  )

  local built_app="$APP_DIR/src-tauri/target/$rust_target/release/bundle/macos/MediaMind.app"
  if [ ! -d "$built_app" ]; then
    log_error "Tauri build çıktısı bulunamadı: $built_app"
    exit 1
  fi

  # 2. Python çekirdeği ve araçları paketle (bundle-core)
  log_info "2/3 Python çekirdeği ve medya kütüphaneleri entegre ediliyor..."
  "$APP_DIR/scripts/bundle-core.sh" --app "$built_app" --arch "$arch"

  # 3. Dağıtım klasörüne kopyala ve .zip arşivi üret
  log_info "3/3 Dağıtım paketleri hazırlanıyor..."
  local dest_dir="$DIST_DIR/$target_name"
  mkdir -p "$dest_dir"

  local final_app="$dest_dir/MediaMind.app"
  rm -rf "$final_app"
  cp -R "$built_app" "$final_app"

  local zip_file="$dest_dir/MediaMind-v${VERSION}-macos-${target_name}.zip"
  rm -f "$zip_file"
  log_info "  Sıkıştırılmış arşiv oluşturuluyor: $(basename "$zip_file")..."
  (
    cd "$dest_dir"
    ditto -c -k --sequesterRsrc --keepParent "MediaMind.app" "$zip_file"
  )

  log_success "Build tamamlandı!"
  echo "----------------------------------------------------------"
  echo " Çıktı Klasörü : $dest_dir"
  echo " Uygulama      : $final_app ($(du -sh "$final_app" | awk '{print $1}'))"
  echo " Arşiv (ZIP)   : $zip_file ($(du -sh "$zip_file" | awk '{print $1}'))"
  echo " Mimari Doğrulama : $(file "$final_app/Contents/MacOS/mediamind-app" | cut -d: -f2-)"
  echo "----------------------------------------------------------"

  if [ "$arch" = "x86_64" ]; then
    echo -e "${YELLOW}Not (2012 Intel Mac İçin):${NC}"
    echo "  1. '$zip_file' dosyasını 2012 Mac'inize kopyalayıp açın ve /Applications klasörüne sürükleyin."
    echo "  2. Apple Developer sertifikası olmadan ad-hoc imzalandığı için ilk açılışta"
    echo "     'Geliştiricisi doğrulanamadı' uyarısı çıkarsa:"
    echo "     Uygulamaya Sağ Tık (Control + Tık) -> 'Aç' -> 'Aç' seçin"
    echo "     veya Terminal'de: xattr -cr /Applications/MediaMind.app"
  fi
}

list_builds() {
  echo ""
  log_info "================ Mevcut MediaMind Build'leri ================"
  if [ ! -d "$DIST_DIR" ]; then
    echo "Henüz dist-releases klasöründe derlenmiş bir build bulunmuyor."
    echo "Build almak için: ./scripts/build.sh [intel|arm64|all]"
    return
  fi

  local found=0
  for app in "$DIST_DIR"/*/MediaMind.app; do
    if [ -d "$app" ]; then
      found=1
      local target_folder="$(basename "$(dirname "$app")")"
      local bin="$app/Contents/MacOS/mediamind-app"
      local py="$app/Contents/Resources/core/python/bin/python3"
      local ff="$app/Contents/Resources/core/media/bin/ffmpeg"
      local zip="$(ls "$(dirname "$app")"/*.zip 2>/dev/null | head -1 || true)"
      
      echo "Paket       : $target_folder"
      echo "Yol         : $app"
      echo "Boyut       : $(du -sh "$app" | awk '{print $1}')"
      if [ -f "$bin" ]; then
        echo "Ana İkili   : $(file "$bin" | cut -d: -f2- | xargs)"
      fi
      if [ -f "$py" ]; then
        echo "Python      : $(file "$py" | cut -d: -f2- | xargs)"
      fi
      if [ -f "$ff" ]; then
        echo "FFmpeg      : $(file "$ff" | cut -d: -f2- | xargs)"
      fi
      if [ -n "$zip" ]; then
        echo "ZIP Paketi  : $(basename "$zip") ($(du -sh "$zip" | awk '{print $1}'))"
      fi
      echo "-------------------------------------------------------------"
    fi
  done

  if [ "$found" -eq 0 ]; then
    echo "dist-releases klasöründe tamamlanmış bir .app paketi bulunamadı."
  fi
}

clean_builds() {
  log_info "Derleme ve dağıtım çıktıları temizleniyor..."
  rm -rf "$DIST_DIR"
  rm -rf "$APP_DIR/src-tauri/target/x86_64-apple-darwin"
  rm -rf "$APP_DIR/src-tauri/target/aarch64-apple-darwin"
  log_success "Temizlik tamamlandı."
}

show_help() {
  echo "MediaMind Çoklu Mimari Build Yöneticisi"
  echo ""
  echo "Kullanım:"
  echo "  $0 <komut>"
  echo ""
  echo "Komutlar:"
  echo "  intel | x86_64     2012 ve sonrası Intel Mac'ler için build alır (macOS 10.15+ uyumlu)"
  echo "  arm64 | apple      Apple Silicon (M1/M2/M3/M4) Mac'ler için build alır"
  echo "  all                Hem Intel hem Apple Silicon için sırayla build alır"
  echo "  list | status      Mevcut build'leri ve mimari durumlarını listeler"
  echo "  clean              Dağıtım klasörünü (dist-releases) ve hedef önbelleklerini temizler"
  echo "  help               Bu yardım mesajını gösterir"
  echo ""
  echo "Örnekler:"
  echo "  ./scripts/build.sh intel"
  echo "  ./scripts/build.sh arm64"
  echo "  ./scripts/build.sh list"
}

CMD="${1:-help}"
case "$CMD" in
  intel|x86_64)
    build_architecture "x86_64"
    ;;
  arm64|apple|apple-silicon|aarch64)
    build_architecture "aarch64"
    ;;
  all)
    build_architecture "aarch64"
    build_architecture "x86_64"
    list_builds
    ;;
  list|status)
    list_builds
    ;;
  clean)
    clean_builds
    ;;
  help|--help|-h)
    show_help
    ;;
  *)
    log_error "Bilinmeyen komut: $CMD"
    show_help
    exit 1
    ;;
esac

