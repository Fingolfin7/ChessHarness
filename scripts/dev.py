"""Start the ChessHarness backend and Vite frontend together."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _npm_command() -> str:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise RuntimeError("npm was not found on PATH. Install Node.js, then try again.")
    return npm


def _start(name: str, command: list[str], cwd: Path) -> subprocess.Popen:
    print(f"[dev] starting {name}: {' '.join(command)}", flush=True)
    return subprocess.Popen(command, cwd=cwd)


def _terminate(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()


def _stop(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for name, process in processes:
        if process.poll() is None:
            print(f"[dev] stopping {name}", flush=True)
            _terminate(process)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if all(process.poll() is not None for _, process in processes):
            return
        time.sleep(0.1)

    for name, process in processes:
        if process.poll() is None:
            print(f"[dev] killing {name}", flush=True)
            process.kill()


def main() -> int:
    npm = _npm_command()
    backend = _start("backend", [sys.executable, "web_main.py"], ROOT)
    frontend = _start("frontend", [npm, "run", "dev"], FRONTEND)
    processes = [("backend", backend), ("frontend", frontend)]

    def handle_stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_stop)

    try:
        while True:
            for name, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"[dev] {name} exited with code {return_code}", flush=True)
                    _stop(processes)
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[dev] shutting down", flush=True)
        _stop(processes)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
