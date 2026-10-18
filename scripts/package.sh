#!/usr/bin/env bash
set -eo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_ARGS=()
EXEC_ARGS=()
BUILD_ARGS=()
SELECTED_PYTHON=""
CONDA_ENVIRONMENT=""
CURRENT_ENVIRONMENT=0
SKIP_INSTALL=0
SELECTION_COUNT=0

require_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "ERROR: $1 needs a value." >&2
        exit 2
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --current-environment)
            CURRENT_ENVIRONMENT=1
            SELECTION_COUNT=$((SELECTION_COUNT + 1))
            ENV_ARGS+=(--current-environment)
            EXEC_ARGS+=(--current-environment)
            shift ;;
        --python)
            require_value "$@"
            SELECTED_PYTHON="$2"
            SELECTION_COUNT=$((SELECTION_COUNT + 1))
            ENV_ARGS+=(--python "$2")
            shift 2 ;;
        --conda-environment|--environment-name)
            require_value "$@"
            CONDA_ENVIRONMENT="$2"
            SELECTION_COUNT=$((SELECTION_COUNT + 1))
            ENV_ARGS+=(--conda-environment "$2")
            EXEC_ARGS+=(--conda-environment "$2")
            shift 2 ;;
        --output-directory)
            require_value "$@"
            BUILD_ARGS+=(--output-directory "$2")
            shift 2 ;;
        --dmg)
            BUILD_ARGS+=(--dmg)
            shift ;;
        --skip-install)
            SKIP_INSTALL=1
            shift ;;
        -h|--help)
            echo 'Usage: ./scripts/package.sh [--current-environment | --python PATH | --conda-environment NAME] [--dmg] [--output-directory PATH] [--skip-install]'
            echo 'Default: build in project .venv. Existing Conda environments are optional. Build on the target macOS architecture.'
            exit 0 ;;
        *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'ERROR: Build macOS applications on macOS. On Windows use scripts/package.ps1.' >&2
    exit 2
fi
if [[ "$SELECTION_COUNT" -gt 1 ]]; then
    echo 'ERROR: Choose only one environment selection option.' >&2
    exit 2
fi
if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    bash "$SCRIPT_DIR/install.sh" "${ENV_ARGS[@]}"
    BUILD_ARGS+=(--skip-prepare)
else
    BUILD_ARGS+=(--skip-install)
fi

BOOTSTRAP=()
if [[ -n "$CONDA_ENVIRONMENT" ]]; then
    BOOTSTRAP=("${CONDA_EXE:-conda}" run --no-capture-output -n "$CONDA_ENVIRONMENT" python)
elif [[ -n "$SELECTED_PYTHON" ]]; then
    BOOTSTRAP=("$SELECTED_PYTHON")
elif [[ "$CURRENT_ENVIRONMENT" -eq 0 && -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    BOOTSTRAP=("$PROJECT_ROOT/.venv/bin/python")
else
    CANDIDATES=(python3 python python3.12 python3.11 python3.13)
    if [[ "$CURRENT_ENVIRONMENT" -eq 1 ]]; then
        CANDIDATES=(python python3)
    fi
    for CANDIDATE in "${CANDIDATES[@]}"; do
        if command -v "$CANDIDATE" >/dev/null 2>&1; then
            BOOTSTRAP=("$CANDIDATE")
            break
        fi
    done
fi
if [[ "${#BOOTSTRAP[@]}" -eq 0 ]]; then
    echo 'ERROR: Source packaging needs Python 3.11-3.13. Installed apps do not need Python or Conda.' >&2
    exit 1
fi
cd -- "$PROJECT_ROOT"
exec "${BOOTSTRAP[@]}" "$SCRIPT_DIR/manage.py" exec "${EXEC_ARGS[@]}" -- "$SCRIPT_DIR/package.py" "${BUILD_ARGS[@]}"
