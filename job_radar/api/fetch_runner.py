"""
Runs `python run.py fetch` in the background for the dashboard's Fetch button.

A fetch takes minutes, far longer than a web request should stay open, so
the button starts it and the page polls `status()` for progress.

It runs as a separate process, exactly as typed in a terminal, rather than
calling runner.process() in a thread: the web server stays responsive, a
crash can't take it down, and there's only one fetch code path to maintain.
Progress comes from reading the process's log lines as they appear; every
line is also appended to fetch.log, the same file the Mac scheduler writes.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FetchRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"running": False}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self, force_rescore: bool) -> bool:
        """Start a fetch; False if one is already running."""
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {
                "running": True,
                "phase": "Starting…",
                "to_score": None,
                "scored": 0,
                "stats": None,
                "error": "",
            }
        cmd = [sys.executable, str(PROJECT_ROOT / "run.py"), "fetch"]
        if force_rescore:
            cmd.append("--force-rescore")
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # logging writes to stderr
            text=True,
        )
        threading.Thread(target=self._follow, args=(proc,), daemon=True).start()
        return True

    def _follow(self, proc: subprocess.Popen) -> None:
        last = ""
        with open(PROJECT_ROOT / "fetch.log", "a", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                last = line.strip() or last
                self._read(line)
        code = proc.wait()
        with self._lock:
            self._state["running"] = False
            if code != 0:
                self._state["error"] = last or f"fetch exited with code {code}"

    def _read(self, line: str) -> None:
        # Matches the log messages in run.py and runner.py
        with self._lock:
            s = self._state
            if "Fetching from" in line:
                s["phase"] = "Fetching from " + line.split("Fetching from", 1)[1].strip().rstrip(".")
            elif "Total jobs across all sources:" in line:
                total = line.rsplit(":", 1)[1].strip()
                s["phase"] = f"Filtering {total} jobs"
            elif "will score:" in line:
                s["to_score"] = int(line.rsplit(":", 1)[1])
                s["phase"] = "Scoring"
            elif ": Scored [" in line:
                s["scored"] += 1
            elif "Done. " in line:
                try:
                    s["stats"] = ast.literal_eval(line.split("Done. ", 1)[1].strip())
                except (ValueError, SyntaxError):
                    pass
