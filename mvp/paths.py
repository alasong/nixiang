"""项目根路径解析。

所有路径都在这里定义，不依赖当前工作目录 —— 在 Mac 上从任何目录执行
`python3 -m mvp` 或 `make demo`，产物都落在同一处。

环境变量覆盖：
    HARNESS_LAB_HOME    项目根（默认：本文件的上两级目录）
    HARNESS_LAB_RUNS    产物根（默认：<root>/runs）
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT: Path = Path(
    os.environ.get("HARNESS_LAB_HOME") or Path(__file__).resolve().parent.parent
).expanduser().resolve()

DATA_DIR: Path = ROOT / "data"
DOCS_DIR: Path = ROOT / "docs"
RUNS_DIR: Path = Path(os.environ.get("HARNESS_LAB_RUNS") or ROOT / "runs").expanduser().resolve()
