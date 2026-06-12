#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${JIMMORIA_VENV_DIR:-$REPO_DIR/.venv}"
BIN_DIR="${JIMMORIA_BIN_DIR:-$HOME/.local/bin}"
EXTRAS="${JIMMORIA_INSTALL_EXTRAS:-all}"

fail() {
  echo "error: $*" >&2
  exit 1
}

pick_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  fail "Python 3.11+ is required. On Ubuntu/WSL, install it with: sudo apt install python3 python3-venv python3-pip"
}

PYTHON="$(pick_python)"

"$PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY

if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  fail "Existing venv is not a Linux/WSL venv: $VENV_DIR. Remove it or set JIMMORIA_VENV_DIR to another path."
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Creating virtual environment: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR" || fail "Could not create venv. On Ubuntu/WSL, install venv support with: sudo apt install python3-venv"
fi

INSTALL_TARGET="$REPO_DIR"
if [ -n "$EXTRAS" ]; then
  INSTALL_TARGET="$INSTALL_TARGET[$EXTRAS]"
fi

echo "Installing JIMMORIA into: $VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$INSTALL_TARGET"

mkdir -p "$BIN_DIR"
ln -sfn "$VENV_DIR/bin/jimmoria" "$BIN_DIR/jimmoria"
ln -sfn "$VENV_DIR/bin/crypto-research" "$BIN_DIR/crypto-research"

update_shell_rc() {
  local rc_file="$1"
  local path_line

  [ -n "$rc_file" ] || return 0
  touch "$rc_file"

  if grep -Fq "# JIMMORIA local CLI" "$rc_file"; then
    return 0
  fi

  if [ "$BIN_DIR" = "$HOME/.local/bin" ]; then
    path_line='export PATH="$HOME/.local/bin:$PATH"'
  else
    path_line="export PATH=\"$BIN_DIR:\$PATH\""
  fi

  {
    echo ""
    echo "# JIMMORIA local CLI"
    echo "$path_line"
  } >> "$rc_file"
}

if [ "${JIMMORIA_SKIP_PATH_UPDATE:-0}" != "1" ]; then
  case "${SHELL:-}" in
    */zsh) update_shell_rc "$HOME/.zshrc" ;;
    */bash) update_shell_rc "$HOME/.bashrc" ;;
    *)
      if [ -f "$HOME/.zshrc" ]; then
        update_shell_rc "$HOME/.zshrc"
      else
        update_shell_rc "$HOME/.bashrc"
      fi
      ;;
  esac
fi

export PATH="$BIN_DIR:$PATH"
jimmoria --help >/dev/null

cat <<EOF

JIMMORIA installed.

Command:
  $BIN_DIR/jimmoria

Run now:
  jimmoria

If this shell cannot find it yet:
  source ~/.bashrc

EOF
