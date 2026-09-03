"""Fail a Railway deployment before startup if compile/tests do not pass."""

import compileall
import subprocess
import sys


def main():
    print("[preflight] compiling repository Python files...")
    if not compileall.compile_dir(".", quiet=1, maxlevels=4):
        print("[preflight] Python compilation failed.", file=sys.stderr)
        return 1
    print("[preflight] running unit and smoke tests...")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        check=False,
    )
    if result.returncode:
        print("[preflight] tests failed; production process will not start.", file=sys.stderr)
        return result.returncode
    print("[preflight] validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
