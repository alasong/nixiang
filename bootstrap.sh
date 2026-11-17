#!/usr/bin/env bash
# 一键初始化（macOS / Linux）。从项目根执行：
#     ./bootstrap.sh          建 venv + 装分析依赖 + 跑测试 + 跑 demo
#     ./bootstrap.sh --core   只跑核心（不装 numpy/pandas，最快）
set -euo pipefail
cd "$(dirname "$0")"

CORE_ONLY=0
[[ "${1:-}" == "--core" ]] && CORE_ONLY=1

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || {
  echo "找不到 $PY。macOS 请先安装：brew install python@3.12"
  exit 1
}

"$PY" - <<'EOF' || exit 1
import sys
if sys.version_info < (3, 10):
    sys.exit(
        f"需要 Python 3.10+，当前 {sys.version.split()[0]}\n"
        "macOS 可用：brew install python@3.12"
    )
EOF

if [ ! -d .venv ]; then
  echo "==> 创建虚拟环境 .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip

if [ "$CORE_ONLY" -eq 0 ]; then
  echo "==> 安装分析依赖（numpy / pandas / pyarrow / scikit-learn）"
  if ! python -m pip install -q -r requirements.txt; then
    echo "!! 依赖安装失败。核心功能不需要它们，可用 ./bootstrap.sh --core 跳过。"
    exit 1
  fi
else
  echo "==> 跳过分析依赖（--core）"
fi

echo "==> 运行测试"
python -m unittest mvp.test_mvp mvp.test_integration

echo "==> 运行三路径对照实验"
python -m mvp

echo
echo "完成。产物在 runs/"
