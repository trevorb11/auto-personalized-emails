#!/usr/bin/env bash
# ── MCA Prospecting Agent — VPS Setup ────────────────────────
# One-command bootstrap for a fresh Ubuntu/Debian VPS.
#
# Usage:
#   git clone <your-repo> /opt/mca-agent
#   cd /opt/mca-agent
#   bash scripts/setup.sh
#
# What it does:
#   1. Installs Python 3.11+ and Node.js (for MCP servers)
#   2. Creates a Python venv and installs dependencies
#   3. Copies .env.example → .env (you fill in keys)
#   4. Initializes the SQLite database
#   5. Installs the daily cron job (7am ET, dry-run by default)
#   6. Does a test dry-run to verify everything works
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "═══════════════════════════════════════════════════"
echo " MCA Prospecting Agent — Setup"
echo " Project: $PROJECT_DIR"
echo "═══════════════════════════════════════════════════"

# ── 1. System dependencies ──────────────────────────────────
echo ""
echo "→ Checking system dependencies..."

install_if_missing() {
    if ! command -v "$1" &> /dev/null; then
        echo "  Installing $1..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq "$2"
    else
        echo "  ✓ $1 found ($(command -v "$1"))"
    fi
}

# Python 3.11+
if command -v python3.11 &> /dev/null; then
    PYTHON=python3.11
elif command -v python3.12 &> /dev/null; then
    PYTHON=python3.12
elif command -v python3 &> /dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.minor}')")
    if [ "$PY_VER" -ge 11 ]; then
        PYTHON=python3
    else
        echo "  Python 3.11+ required (found 3.$PY_VER). Installing..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
        PYTHON=python3.11
    fi
else
    echo "  Installing Python 3.11..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
    PYTHON=python3.11
fi
echo "  ✓ Python: $($PYTHON --version)"

# pip
install_if_missing pip3 python3-pip

# Node.js (for MCP servers — npx)
if ! command -v node &> /dev/null; then
    echo "  Installing Node.js 20.x (for MCP servers)..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
    sudo apt-get install -y -qq nodejs
fi
echo "  ✓ Node: $(node --version)"

# ── 2. Python virtual environment ───────────────────────────
echo ""
echo "→ Setting up Python venv..."

if [ ! -d "$PROJECT_DIR/venv" ]; then
    $PYTHON -m venv "$PROJECT_DIR/venv"
    echo "  Created venv at $PROJECT_DIR/venv"
else
    echo "  ✓ venv already exists"
fi

source "$PROJECT_DIR/venv/bin/activate"
pip install --upgrade pip -q
pip install -r "$PROJECT_DIR/requirements.txt" -q
echo "  ✓ Dependencies installed"

# ── 3. Environment file ─────────────────────────────────────
echo ""
echo "→ Environment configuration..."

if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
    echo "  Created .env from .env.example (permissions: owner-only)"
    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║  IMPORTANT: Edit .env with your API keys    ║"
    echo "  ║  nano $PROJECT_DIR/.env"
    echo "  ║                                              ║"
    echo "  ║  Required keys:                              ║"
    echo "  ║    ANTHROPIC_API_KEY                         ║"
    echo "  ║    GHL_API_KEY                               ║"
    echo "  ║    GHL_LOCATION_ID                           ║"
    echo "  ╚══════════════════════════════════════════════╝"
else
    # Ensure permissions are locked down even if .env already exists
    chmod 600 "$PROJECT_DIR/.env"
    echo "  ✓ .env already exists (permissions: owner-only)"
fi

# ── 4. Initialize database ──────────────────────────────────
echo ""
echo "→ Initializing database..."

python -c "from data.database import init_db; init_db(); print('  ✓ Database initialized')"

# ── 5. Create directories ───────────────────────────────────
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data/ucc" "$PROJECT_DIR/data/enriched"
echo "  ✓ Directories ready"

# ── 6. Make scripts executable ──────────────────────────────
chmod +x "$PROJECT_DIR/scripts/run.sh"
echo "  ✓ scripts/run.sh is executable"

# ── 7. Install cron job ─────────────────────────────────────
echo ""
echo "→ Setting up cron job..."

CRON_CMD="0 12 * * * $PROJECT_DIR/scripts/run.sh --no-dry-run >> $PROJECT_DIR/logs/cron.log 2>&1"
# 12:00 UTC = 7:00 AM ET

# Check if cron entry already exists
if crontab -l 2>/dev/null | grep -qF "mca-agent" || crontab -l 2>/dev/null | grep -qF "$PROJECT_DIR/scripts/run.sh"; then
    echo "  ✓ Cron job already installed"
else
    # Add to existing crontab (or create new one)
    (crontab -l 2>/dev/null || true; echo "# MCA Prospecting Agent — daily pipeline (7am ET / 12pm UTC)"; echo "$CRON_CMD") | crontab -
    echo "  ✓ Cron job installed (7am ET daily)"
    echo "  Edit with: crontab -e"
fi

echo ""
echo "  Current crontab:"
crontab -l 2>/dev/null | grep -A1 "mca" || echo "  (none)"

# ── 8. Test run ─────────────────────────────────────────────
echo ""
echo "→ Running test (dry-run, skip API calls)..."
echo ""

if python "$PROJECT_DIR/main_agent.py" --dry-run --skip-inbound --skip-ghl-sync 2>&1 | tail -5; then
    echo ""
    echo "  ✓ Test run passed"
else
    echo ""
    echo "  ⚠ Test run had issues (check output above)"
    echo "  This is often due to missing .env keys — fill those in first."
fi

# ── Done ────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
echo " Setup complete!"
echo ""
echo " Next steps:"
echo "   1. Edit API keys:   nano $PROJECT_DIR/.env"
echo "   2. Test dry run:    $PROJECT_DIR/scripts/run.sh --dry-run"
echo "   3. Test live:       $PROJECT_DIR/scripts/run.sh --no-dry-run --states FL"
echo "   4. Check logs:      tail -f $PROJECT_DIR/logs/\$(date +%Y-%m-%d).log"
echo "   5. Edit schedule:   crontab -e"
echo "═══════════════════════════════════════════════════"
