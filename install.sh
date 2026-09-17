#!/usr/bin/env bash
set -euo pipefail

APP_NAME="DevMesh Studio"
APP_SLUG="devmesh-studio"
REPO_URL="${DEVMESH_REPO_URL:-https://github.com/rzgrozt/devmesh-studio.git}"
BRANCH="${DEVMESH_BRANCH:-main}"
APP_DIR="${DEVMESH_INSTALL_DIR:-$HOME/.local/opt/$APP_SLUG}"
SCRIPT_DIR=""

if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

say() { printf '[DevMesh] %s\n' "$*"; }
die() { printf '[DevMesh] ERROR: %s\n' "$*" >&2; exit 1; }

require_python() {
  command -v python3 >/dev/null 2>&1 || die "Python 3.11+ is required."
  python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 11):
    raise SystemExit("DevMesh Studio requires Python 3.11 or newer.")
PY
}

manager() {
  local root="$1"; shift
  local py="$root/.venv/bin/python"
  if [[ -x "$py" ]]; then
    exec "$py" "$root/scripts/bootstrap.py" "$@"
  fi
  exec python3 "$root/scripts/bootstrap.py" "$@"
}

case "${1:-install}" in
  uninstall)
    require_python
    if [[ -f "$APP_DIR/scripts/bootstrap.py" ]]; then
      manager "$APP_DIR" uninstall "${@:2}"
    fi
    say "DevMesh is not installed at $APP_DIR"
    exit 0
    ;;
  purge)
    require_python
    if [[ -f "$APP_DIR/scripts/bootstrap.py" ]]; then
      manager "$APP_DIR" uninstall --purge
    fi
    say "DevMesh is not installed at $APP_DIR"
    exit 0
    ;;
  upgrade)
    require_python
    if [[ -f "$APP_DIR/scripts/bootstrap.py" ]]; then
      manager "$APP_DIR" upgrade "${@:2}"
    fi
    die "DevMesh is not installed at $APP_DIR"
    ;;
  install|"")
    ;;
  *)
    die "Unknown action '$1'. Use install, upgrade, uninstall, or purge."
    ;;
esac

require_python

# When install.sh is executed from a source checkout, install that checkout.
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/pyproject.toml" && -f "$SCRIPT_DIR/scripts/bootstrap.py" ]]; then
  say "Installing from local checkout: $SCRIPT_DIR"
  python3 "$SCRIPT_DIR/scripts/bootstrap.py" install --source "$SCRIPT_DIR"
  exit 0
fi

command -v git >/dev/null 2>&1 || die "Git is required for the one-line installer."

say "Installing $APP_NAME from $REPO_URL ($BRANCH)"
mkdir -p "$(dirname "$APP_DIR")"

if [[ -d "$APP_DIR/.git" ]]; then
  say "Existing checkout found; refreshing it"
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout -q "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
elif [[ -e "$APP_DIR" ]]; then
  die "$APP_DIR already exists but is not a Git checkout. Remove it or set DEVMESH_INSTALL_DIR."
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

python3 "$APP_DIR/scripts/bootstrap.py" install --source "$APP_DIR"

say "Done. Run: devmesh"
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  say "Add $HOME/.local/bin to PATH if the devmesh command is not found."
fi
