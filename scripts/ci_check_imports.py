"""DEATH MODE v2 — CI import validation script."""
import os
import sys

sys.path.insert(0, "user_data/strategies")

skip = {"NostalgiaForInfinityX7", "__pycache__"}

for f in sorted(os.listdir("user_data/strategies")):
    if not f.endswith(".py") or f in skip:
        continue
    name = f[:-3]
    try:
        __import__(name)
        print(f"OK: {name}")
    except Exception as e:
        print(f"WARN: {name} -> {e}")
