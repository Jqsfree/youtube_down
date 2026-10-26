#!/usr/bin/env python3
"""兼容旧路径：``python scripts/download_cli.py ...`` 等同于 ``youtube-down-cli ...``。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from download_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
