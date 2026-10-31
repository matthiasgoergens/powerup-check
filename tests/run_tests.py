#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Run powerup_check.py on every design in tests/ and compare the verdict with the expected one.

    tests/run_tests.py WORKDIR
"""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
# design: (expected verdict prefix, extra arguments)
EXPECT = {
    "good": ("**Deterministic**", []),
    "dead": ("**Deterministic**", []),
    "ram": ("**Deterministic for", []),
    "bbram": ("**Deterministic**", ["--lib", str(HERE / "bb_macro.v")]),
    "leaky": ("**Not deterministic**", []),
    "initval": ("**Not deterministic**", []),
    "async_leak": ("**Not deterministic**", []),
    "partial": ("**Not deterministic**", []),
    "divclk": ("**Not deterministic**", []),
    "inreset": ("**Not deterministic**", []),
    "late": ("**Not deterministic**", []),
}


def one(name, work):
    want, extra = EXPECT[name]
    r = subprocess.run([sys.executable, str(HERE.parent / "powerup_check.py"), str(HERE / f"{name}.v"),
                        "--top", name, "--work", str(work / name), "--timeout", "600", *extra],
                       capture_output=True, text=True)
    lines = r.stdout.splitlines()
    got = lines[2] if len(lines) > 2 else f"(no report; rc={r.returncode}) {r.stderr[-300:]}"
    return name, got.startswith(want), want, got


work = Path(sys.argv[1]).resolve()
with ThreadPoolExecutor(max_workers=3) as ex:
    results = list(ex.map(lambda n: one(n, work), EXPECT))
for name, ok, want, got in results:
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got}" + ("" if ok else f"  (expected {want})"))
sys.exit(0 if all(ok for _, ok, *_ in results) else 1)
