#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ENV_NAME="${FILM_CONDA_ENV:-base}"
CONDA_BIN="${CONDA_EXE:-}"

if [[ -z "$CONDA_BIN" ]]; then
  CONDA_BIN="$(command -v conda || true)"
fi

if [[ -z "$CONDA_BIN" ]]; then
  echo "找不到 Conda。请先将 conda 加入 PATH，或直接在已激活环境中运行 check_stock.sh。" >&2
  exit 1
fi

CONDA_BASE="$("$CONDA_BIN" info --base)"
CONDA_INIT="$CONDA_BASE/etc/profile.d/conda.sh"

if [[ ! -f "$CONDA_INIT" ]]; then
  echo "找不到 Conda 初始化脚本：$CONDA_INIT" >&2
  exit 1
fi

source "$CONDA_INIT"
conda activate "$CONDA_ENV_NAME"
exec "$SCRIPT_DIR/check_stock.sh" "$@"
