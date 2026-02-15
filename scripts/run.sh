#!/usr/bin/env bash
# ── MCA Prospecting Agent — Cron Runner ──────────────────────
# This script is called by cron. It activates the Python venv,
# runs the daily pipeline, and logs output with rotation.
#
# Usage:
#   ./scripts/run.sh                  # dry-run mode (default)
#   ./scripts/run.sh --no-dry-run     # live mode (writes to GHL)
#   ./scripts/run.sh --states FL,TX   # specific states only
#
# Cron example (7am ET daily):
#   0 7 * * * /opt/mca-agent/scripts/run.sh --no-dry-run
#
set -euo pipefail

# ── Resolve project root ────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# ── Logging ─────────────────────────────────────────────────
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── Activate venv ───────────────────────────────────────────
if [ -d "$PROJECT_DIR/venv" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
elif [ -d "$PROJECT_DIR/.venv" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
else
    log "ERROR: No Python venv found. Run scripts/setup.sh first."
    exit 1
fi

# ── Run the pipeline ────────────────────────────────────────
log "Starting MCA pipeline"
log "Args: $*"
log "Python: $(which python) ($(python --version 2>&1))"

if python "$PROJECT_DIR/main_agent.py" "$@" >> "$LOG_FILE" 2>&1; then
    log "Pipeline completed successfully"
else
    EXIT_CODE=$?
    log "ERROR: Pipeline exited with code $EXIT_CODE"

    # Send a Slack alert if webhook is configured
    SLACK_URL=$(grep -s '^SLACK_WEBHOOK_URL=' "$PROJECT_DIR/.env" | cut -d= -f2-)
    if [ -n "$SLACK_URL" ] && [ "$SLACK_URL" != "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" ]; then
        curl -s -X POST "$SLACK_URL" \
            -H 'Content-type: application/json' \
            -d "{\"text\":\"MCA Agent FAILED (exit $EXIT_CODE). Check $LOG_FILE\"}" \
            > /dev/null 2>&1 || true
    fi

    exit $EXIT_CODE
fi

# ── Log rotation (keep 30 days) ─────────────────────────────
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
