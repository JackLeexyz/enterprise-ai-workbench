#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PYTHON="$ROOT/.venv/bin/python3"
elif [[ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]]; then
  PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
else
  PYTHON="$(command -v python3)"
fi
if ! "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 9); import docx'; then
  printf '%s\n' '需要 Python 3.9+ 和 requirements.txt 中的依赖。尚未启动服务。'
  exit 1
fi
if [[ ! -f "$ROOT/data/workbench.db" ]]; then
  "$PYTHON" setup_app.py
fi
exec "$PYTHON" start_local.py
