#!/bin/sh
# Default: bootstrap pinned source/dependencies/browser, then build safely.
# --offline: require existing local prerequisites; never download or install a browser.
set -eu
MODE=bootstrap
case "${1:-}" in
  "") ;;
  --offline) MODE=offline ;;
  *) echo 'Usage: scripts/install_maps.sh [--offline]' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo 'Unexpected arguments' >&2; exit 2; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PIN=549e4b5e61c7103685ef8392f246ebdba783ed03
[ "${MAPS_COMMIT:-$PIN}" = "$PIN" ] || {
  echo "Stability patch only supports $PIN; no checkout changed" >&2; exit 2;
}
SRC="$ROOT/vendor/google-maps-scraper-$PIN"
BIN="$ROOT/tools/bin"
if [ "$MODE" = offline ] && [ ! -e "$SRC/.git" ]; then
  echo "Missing local pinned checkout: $SRC; offline build stopped" >&2
  exit 2
fi
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
[ -x "$PYTHON" ] || PYTHON=$(command -v python3)
GO=${GO:-$(command -v go)}
export GOTOOLCHAIN=local
export GOFLAGS=-mod=readonly GOMAXPROCS=2 DISABLE_TELEMETRY=1 PYTHONDONTWRITEBYTECODE=1
if [ "$MODE" = offline ]; then
  export GOPROXY=off GOSUMDB=off GOVCS='*:off'
fi

mkdir -p "$BIN"
LOCK="$BIN/.maps-build.lock"
mkdir "$LOCK" 2>/dev/null || {
  echo "Another Maps build or stale build lock exists: $LOCK; nothing replaced" >&2; exit 1;
}
CANDIDATE="$BIN/google-maps-scraper.new"
MANIFEST="$BIN/google-maps-scraper.build.json.new"
HELP="$BIN/google-maps-scraper.help.txt.new"
if [ -e "$CANDIDATE" ] || [ -L "$CANDIDATE" ] || [ -e "$MANIFEST" ] || [ -L "$MANIFEST" ] \
   || [ -e "$HELP" ] || [ -L "$HELP" ]; then
  rmdir "$LOCK"
  echo 'Existing .new Maps artifact preserved; inspect it before rebuilding' >&2
  exit 1
fi
BOOTSTRAP_DIR=""
cleanup() {
  rm -f "$CANDIDATE" "$MANIFEST" "$HELP"
  if [ -n "$BOOTSTRAP_DIR" ]; then
    rm -rf "$BOOTSTRAP_DIR"
  fi
  rmdir "$LOCK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Never fetch, checkout, reset or clean an existing vendor tree.
if [ -e "$SRC" ] || [ -L "$SRC" ]; then
  [ -e "$SRC/.git" ] && [ "$(git -C "$SRC" rev-parse HEAD)" = "$PIN" ] || {
    echo "Existing Maps checkout revision mismatch; preserved without changes: $SRC" >&2; exit 2;
  }
else
  [ "$MODE" = bootstrap ] || { echo 'Offline source missing' >&2; exit 2; }
  mkdir -p "$ROOT/vendor"
  BOOTSTRAP_DIR=$(mktemp -d "$ROOT/vendor/.maps-bootstrap.XXXXXX")
  git -C "$BOOTSTRAP_DIR" init
  git -C "$BOOTSTRAP_DIR" remote add origin https://github.com/gosom/google-maps-scraper.git
  git -C "$BOOTSTRAP_DIR" fetch --depth 1 origin "$PIN"
  git -C "$BOOTSTRAP_DIR" checkout --detach "$PIN"
  [ "$(git -C "$BOOTSTRAP_DIR" rev-parse HEAD)" = "$PIN" ] || {
    echo 'Fetched Maps revision mismatch' >&2; exit 2;
  }
  [ ! -e "$SRC" ] && [ ! -L "$SRC" ] || { echo 'Maps source appeared during bootstrap; preserved' >&2; exit 2; }
  mv "$BOOTSTRAP_DIR" "$SRC"
  BOOTSTRAP_DIR=""
fi
"$PYTHON" -B "$ROOT/scripts/patch_maps_stability.py" "$SRC"

# Prefer the exact cached compiler. Only bootstrap mode may acquire a missing one.
REQUIRED_GO=$(awk '$1 == "go" { print $2; exit }' "$SRC/go.mod")
case "$("$GO" version)" in
  *" go$REQUIRED_GO "*) ;;
  *)
    CACHED_GO="$("$GO" env GOMODCACHE)/golang.org/toolchain@v0.0.1-go$REQUIRED_GO.$("$GO" env GOHOSTOS)-$("$GO" env GOHOSTARCH)/bin/go"
    if [ -x "$CACHED_GO" ]; then
      GO="$CACHED_GO"
    elif [ "$MODE" = bootstrap ]; then
      TOOLCHAIN_ROOT=$(GOTOOLCHAIN="go$REQUIRED_GO" "$GO" env GOROOT)
      GO="$TOOLCHAIN_ROOT/bin/go"
    else
      echo "Go $REQUIRED_GO is not cached; set GO to a locally installed matching compiler" >&2
      exit 2
    fi
    ;;
esac
case "$("$GO" version)" in
  *" go$REQUIRED_GO "*) ;;
  *) echo 'Go compiler version mismatch' >&2; exit 2 ;;
esac
# go.mod replaces scrapemate with a patched copy (upstream Chromium flags crash on macOS).
"$PYTHON" -B "$ROOT/scripts/patch_maps_stability.py" "$SRC" --prepare-scrapemate --go "$GO"
cd "$SRC"
if [ "$MODE" = bootstrap ]; then
  "$GO" mod download
fi
# Compile and verify from the prepared cache in both modes.
export GOPROXY=off GOSUMDB=off GOVCS='*:off'
SOURCE_DIGEST=$("$PYTHON" -B "$ROOT/scripts/patch_maps_stability.py" "$SRC" --source-digest)
"$GO" mod verify
"$GO" test -p 2 -race ./gmaps ./runner -count=1
"$GO" build -p 2 -trimpath -buildvcs=false -o "$CANDIDATE" .
"$PYTHON" -B "$ROOT/scripts/patch_maps_stability.py" "$SRC" \
  --manifest "$CANDIDATE" "$MANIFEST" --expected-source "$SOURCE_DIGEST"
if [ "$MODE" = bootstrap ]; then
  export PLAYWRIGHT_DRIVER_PATH="${PLAYWRIGHT_DRIVER_PATH:-$BIN/playwright-driver}"
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$BIN/playwright-browsers}"
  PLAYWRIGHT_INSTALL_ONLY=1 "$CANDIDATE"
  PLAYWRIGHT_INSTALL_ONLY=0 "$CANDIDATE" -h > "$HELP" 2>&1
fi

# Both staged artifacts have passed validation. Same-directory rename is atomic.
mv "$CANDIDATE" "$BIN/google-maps-scraper"
mv "$MANIFEST" "$BIN/google-maps-scraper.build.json"
if [ -f "$HELP" ]; then
  mv "$HELP" "$BIN/google-maps-scraper.help.txt"
fi
printf 'Replaced Maps binary with verified stability build (%s; %s). No collection started.\n' "$PIN" "$MODE"
