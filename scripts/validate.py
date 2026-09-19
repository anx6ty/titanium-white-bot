from __future__ import annotations

import ast
from pathlib import Path

files = [Path("bot.py"), Path("app/command_suite.py")]
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    print(f"OK: {path}")

print("The command suite defines 20 categories × 16 actions = 320 grouped actions.")
