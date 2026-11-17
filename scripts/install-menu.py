#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop.launcher import install  # noqa: E402

print(f"已加入應用程式選單：{install()}")
