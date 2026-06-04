#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${SDK_DIR}/.venv"
REQUIREMENTS="${SDK_DIR}/requirements.txt"
export PIP_CACHE_DIR="${SDK_DIR}/.pip-cache"

if [[ "${1:-}" == "--dev" ]]; then
  REQUIREMENTS="${SDK_DIR}/requirements-dev.txt"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

cd "${SDK_DIR}"
"${VENV_DIR}/bin/python" -m pip install -r "${REQUIREMENTS}"

echo "venv: ${VENV_DIR}"
echo "python: ${VENV_DIR}/bin/python"
echo "tui: ${VENV_DIR}/bin/sl-tui"
