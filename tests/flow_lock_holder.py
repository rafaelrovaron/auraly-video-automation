from __future__ import annotations

from pathlib import Path
import sys

from auraly_pipeline.flow.lock import BrowserRuntimeLock


def main() -> int:
    lock = BrowserRuntimeLock(Path(sys.argv[1]))
    with lock:
        print("locked", flush=True)
        sys.stdin.readline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
