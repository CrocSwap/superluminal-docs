#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${SDK_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "missing ${PYTHON_BIN}" >&2
  echo "run ./scripts/bootstrap_venv.sh --dev first" >&2
  exit 1
fi

PYTHONPATH="${SDK_DIR}" "${PYTHON_BIN}" -m unittest discover -s "${SDK_DIR}/test"
PYTHONPATH="${SDK_DIR}" "${PYTHON_BIN}" -m mypy --config-file "${SDK_DIR}/pyproject.toml" "${SDK_DIR}/dfba_client"
"${PYTHON_BIN}" -m ruff check "${SDK_DIR}/dfba_client" "${SDK_DIR}/test"
