#!/usr/bin/env bash
# macOS ships Bash 3.2; nounset rejects even declared empty arrays there.
set -eo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SELECTED_PYTHON=""
CURRENT_ENVIRONMENT=0
CONDA_ENVIRONMENT=""
ARGS=("$@")
for ((index = 0; index < ${#ARGS[@]}; index++)); do
    case "${ARGS[$index]}" in
        --current-environment) CURRENT_ENVIRONMENT=1 ;;
        --python) SELECTED_PYTHON="${ARGS[$((index + 1))]:-}" ;;
        --python=*) SELECTED_PYTHON="${ARGS[$index]#*=}" ;;
        --conda-environment) CONDA_ENVIRONMENT="${ARGS[$((index + 1))]:-}" ;;
        --conda-environment=*) CONDA_ENVIRONMENT="${ARGS[$index]#*=}" ;;
    esac
done
if [[ -n "$CONDA_ENVIRONMENT" ]]; then
    CONDA_TOOL="${CONDA_EXE:-conda}"
    exec "$CONDA_TOOL" run --no-capture-output -n "$CONDA_ENVIRONMENT" python "$SCRIPT_DIR/manage.py" install "$@"
fi
if [[ -n "$SELECTED_PYTHON" ]]; then
    exec "$SELECTED_PYTHON" "$SCRIPT_DIR/manage.py" install "$@"
fi
if [[ "$CURRENT_ENVIRONMENT" -eq 0 && -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    exec "$PROJECT_ROOT/.venv/bin/python" "$SCRIPT_DIR/manage.py" install "$@"
fi
if [[ "$CURRENT_ENVIRONMENT" -eq 1 ]]; then
    CANDIDATES=(python python3)
else
    CANDIDATES=(python3 python python3.12 python3.11 python3.13)
fi
for CANDIDATE in "${CANDIDATES[@]}"; do
    if command -v "$CANDIDATE" >/dev/null 2>&1; then
        exec "$CANDIDATE" "$SCRIPT_DIR/manage.py" install "$@"
    fi
done
echo 'ERROR: Python 3.11-3.13 is required for source setup. Packaged apps do not need Python or Conda.' >&2
exit 1
