#!/bin/sh
# Run using: sh install.command --hub /absolute/path/to/KnowledgeHub
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    printf '%s\n' 'Create .venv with Python 3.11+ and install mcp_server/requirements.txt first.' >&2
    exit 2
fi
exec "$PYTHON" "$ROOT/scripts/install_clients.py" "$@"
