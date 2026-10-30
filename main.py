#!/usr/bin/env python3
"""TimeCode Studio 入口。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tcs.ui_main import run

if __name__ == "__main__":
    run()
