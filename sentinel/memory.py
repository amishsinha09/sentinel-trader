"""Experiment memory (SQLite): every strategy ever tested, champions per role, holdout use, audit log."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
  hash TEXT PRIMARY KEY, ts TEXT, parent TEXT, name TEXT, version TEXT,
  hypothesis TEXT, agent TEXT, spec TEXT, report TEXT, status TEXT, reasons TEXT
);
CREATE TABLE IF NOT EXISTS champions (role TEXT PRIMARY KEY, hash TEXT, ts TEXT, approved_by TEXT);
CREATE TABLE IF NOT EXISTS holdout_log (ts TEXT, hash TEXT, passed INTEGER, report TEXT);
CREATE TABLE IF NOT EXISTS events (ts TEXT, kind TEXT, message TEXT, data TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Memory:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "experiments.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # experiments -------------------------------------------------------
    def exists(self, h: str) -> bool:
        return self.db.execute("SELECT 1 FROM experiments WHERE hash=?", (h,)).fetchone() is not None

    def record(self, h, spec, report, status, hypothesis="", agent="", parent=None, reasons=()):
        self.db.execute(
            "INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (h, now(), parent, spec.get("name"), spec.get("version"), hypothesis, agent,
             json.dumps(spec), json.dumps(report, default=float), status, json.dumps(list(reasons))))
        self.db.commit()

    def get(self, h: str) -> dict | None:
        r = self.db.execute("SELECT * FROM experiments WHERE hash=?", (h,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("spec", "report", "reasons"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def recent(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT hash FROM experiments ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [self.get(r["hash"]) for r in rows]

    def trial_count(self) -> int:
        """Every evaluated spec counts toward the multiple-testing penalty."""
        return self.db.execute("SELECT COUNT(*) FROM experiments WHERE report != 'null'").fetchone()[0]

    def trial_sr_var(self) -> float:
        srs = []
        for (rep,) in self.db.execute("SELECT report FROM experiments WHERE report != 'null'"):
            try:
                srs.append(json.loads(rep)["validation"]["sr_period"])
            except (KeyError, TypeError, ValueError):
                pass
        return float(np.var(srs, ddof=1)) if len(srs) >= 5 else 0.0

    # champions ---------------------------------------------------------
    def set_champion(self, role: str, h: str, approved_by: str = "system"):
        self.db.execute("INSERT OR REPLACE INTO champions VALUES (?,?,?,?)", (role, h, now(), approved_by))
        self.db.commit()
        self.event("champion", f"{role} champion -> {h}", {"approved_by": approved_by})

    def clear_champion(self, role: str, reason: str):
        self.db.execute("DELETE FROM champions WHERE role=?", (role,))
        self.db.commit()
        self.event("champion", f"{role} champion removed: {reason}")

    def approved_by(self, role: str) -> str | None:
        r = self.db.execute("SELECT approved_by FROM champions WHERE role=?", (role,)).fetchone()
        return r["approved_by"] if r else None

    def is_override(self, role: str) -> bool:
        return (self.approved_by(role) or "").startswith("override")

    def champion(self, role: str) -> dict | None:
        r = self.db.execute("SELECT hash FROM champions WHERE role=?", (role,)).fetchone()
        return self.get(r["hash"]) if r else None

    # audit -------------------------------------------------------------
    def log_holdout(self, h: str, report: dict):
        self.db.execute("INSERT INTO holdout_log VALUES (?,?,?,?)", (now(), h, int(report["passed"]), json.dumps(report, default=float)))
        self.db.commit()

    def holdout_confirmed(self, h: str) -> bool:
        return self.db.execute("SELECT 1 FROM holdout_log WHERE hash=? AND passed=1", (h,)).fetchone() is not None

    def holdout_failed(self, h: str) -> bool:
        return self.db.execute("SELECT 1 FROM holdout_log WHERE hash=? AND passed=0", (h,)).fetchone() is not None

    def holdout_uses(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM holdout_log").fetchone()[0]

    def event(self, kind: str, message: str, data: dict | None = None):
        self.db.execute("INSERT INTO events VALUES (?,?,?,?)", (now(), kind, message, json.dumps(data or {}, default=str)))
        self.db.commit()
