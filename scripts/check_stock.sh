#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

# 解释器优先级：
#   1) FILM_PYTHON —— 显式指定，永远最高
#   2) 项目自带的 .venv —— 免去"忘了激活环境"这类问题
#   3) 当前环境的 python3 / python —— 兼容 conda / 已激活的任意环境
# 之所以把 .venv 排在当前环境之前：脚本依赖 playwright，而"当前 python"
# 很可能是系统自带的那个、装不了也没装依赖，报错信息还很难懂。
if [[ -n "${FILM_PYTHON:-}" ]]; then
  PYTHON_BIN="$FILM_PYTHON"
elif [[ -x "$VENV_PYTHON" ]]; then
  PYTHON_BIN="$VENV_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && [[ ! -x "$PYTHON_BIN" ]]; then
  echo "找不到 Python：$PYTHON_BIN" >&2
  echo "请任选其一：" >&2
  echo "  · 建项目环境：python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  echo "  · 用 Conda：  conda env create -f environment.yml && conda activate film-stock-monitor" >&2
  echo "  · 指定解释器：FILM_PYTHON=/path/to/python $0 $*" >&2
  exit 1
fi

# playwright 只有 --all / headless / dakis 才用得上，缺了不该挡住常规巡检，
# 所以这里只在真正要渲染时才校验，并且给出可直接照抄的修复命令。
if [[ " $* " == *" --all "* || " $* " == *" headless "* || " $* " == *" dakis "* ]]; then
  if ! "$PYTHON_BIN" -c "import playwright" >/dev/null 2>&1; then
    echo "⚠ 当前解释器没有 playwright，headless 与 Dakis 两层会全部记为 BLOCKED。" >&2
    echo "  解释器：$PYTHON_BIN" >&2
    echo "  修复：  $PYTHON_BIN -m pip install -r $PROJECT_DIR/requirements.txt" >&2
    echo "          $PYTHON_BIN -m playwright install chromium" >&2
  fi
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/check_stock.py" "$@"
