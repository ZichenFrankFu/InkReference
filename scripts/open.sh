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
    exec "$CONDA_TOOL" run --no-capture-output -n "$CONDA_ENVIRONMENT" python "$SCRIPT_DIR/manage.py" open "$@"
fi
if [[ -n "$SELECTED_PYTHON" ]]; then
    exec "$SELECTED_PYTHON" "$SCRIPT_DIR/manage.py" open "$@"
fi
if [[ "$CURRENT_ENVIRONMENT" -eq 0 ]]; then
    if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
        echo 'ERROR: The project .venv is missing. Run ./scripts/install.sh, or explicitly use --current-environment.' >&2
        exit 1
    fi
    exec "$PROJECT_ROOT/.venv/bin/python" "$SCRIPT_DIR/manage.py" open "$@"
fi
for CANDIDATE in python python3; do
    if command -v "$CANDIDATE" >/dev/null 2>&1; then
        exec "$CANDIDATE" "$SCRIPT_DIR/manage.py" open "$@"
    fi
done
echo 'ERROR: Python is not available in the selected environment.' >&2
exit 1
