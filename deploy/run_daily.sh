#!/usr/bin/env bash
# Daily trading cycle for macOS / Linux. Schedule for 8:35 AM Central (9:35 ET), Mon-Fri:
#   crontab -e
#   35 8 * * 1-5 /path/to/sentinel-trader/deploy/run_daily.sh
# On a Mac, also allow the machine to wake: System Settings > Battery > Options, or `pmset repeat wakeorpoweron MTWRF 08:30:00`.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
mkdir -p state
python -m sentinel run-once >> state/run.log 2>&1
python -m sentinel report >> state/run.log 2>&1
