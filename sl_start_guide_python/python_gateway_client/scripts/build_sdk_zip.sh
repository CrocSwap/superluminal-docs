#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd -- "${SDK_DIR}/.." && pwd)"
OUT_DIR="${SDK_DIR}/sdk-dist"
ZIP_NAME="dfba-gateway-client-sdk-$(date -u +%Y%m%dT%H%M%SZ).zip"
DOC_STAGING="$(mktemp -d)"

cleanup() {
  rm -rf "${DOC_STAGING}"
}

trap cleanup EXIT

mkdir -p "${OUT_DIR}"
rm -f "${OUT_DIR}/${ZIP_NAME}"

mkdir -p "${DOC_STAGING}/python_gateway_client/docs"
cp "${REPO_ROOT}/dfba_gateway/api.md" "${DOC_STAGING}/python_gateway_client/docs/api.md"
sed 's#../python_gateway_client/#../#g' \
  "${REPO_ROOT}/dfba_gateway/getting_started_with_python.md" \
  > "${DOC_STAGING}/python_gateway_client/docs/getting_started_with_python.md"

cd "${REPO_ROOT}"

zip -r "${OUT_DIR}/${ZIP_NAME}" "$(basename "${SDK_DIR}")" \
  -x 'python_gateway_client/.venv/*' \
  -x 'python_gateway_client/build/*' \
  -x 'python_gateway_client/dist/*' \
  -x 'python_gateway_client/*.egg-info/*' \
  -x 'python_gateway_client/sdk-dist/*' \
  -x 'python_gateway_client/.mypy_cache/*' \
  -x 'python_gateway_client/.ruff_cache/*' \
  -x 'python_gateway_client/.pip-cache/*' \
  -x 'python_gateway_client/**/__pycache__/*' \
  -x 'python_gateway_client/**/*.pyc'

(
  cd "${DOC_STAGING}"
  zip -ur "${OUT_DIR}/${ZIP_NAME}" python_gateway_client/docs
)

echo "${OUT_DIR}/${ZIP_NAME}"
