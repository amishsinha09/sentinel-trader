"""Alerts to Slack / Discord / Teams-style incoming webhooks, plus a local log file."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path


class Alerts:
    def __init__(self, cfg: dict):
        self.url = os.environ.get(cfg.get("alerts", {}).get("webhook_url_env", "ALERT_WEBHOOK_URL"), "")
        self.log = Path(cfg["state_dir"]) / "alerts.log"

    def send(self, message: str, level: str = "info") -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} [{level.upper()}] {message}"
        with self.log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if not self.url:
            return
        body = json.dumps({"text": f"Sentinel {level.upper()}: {message}", "content": f"Sentinel {level.upper()}: {message}"})
        try:
            req = urllib.request.Request(self.url, body.encode(), {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # alerting must never crash trading
            with self.log.open("a", encoding="utf-8") as f:
                f.write(f"  (webhook failed: {e})\n")
