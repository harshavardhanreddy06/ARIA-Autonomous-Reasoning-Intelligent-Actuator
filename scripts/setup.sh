#!/bin/bash
# scripts/setup.sh
# ================
# One-time environment setup for ARIA.
# Run: bash scripts/setup.sh  (from anywhere — self-locates the project root)
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=============================================="
echo " ARIA — Environment Setup"
echo "=============================================="

# ── 1. Detect EnergyPlus install ──────────────────
echo ""
echo "[1] Detecting EnergyPlus..."
EP_DIR=$(find /Applications -maxdepth 1 -name "EnergyPlus*" -type d | sort -r | head -1)

if [ -z "$EP_DIR" ]; then
    echo "    ❌ EnergyPlus not found in /Applications"
    echo "    Download from: https://energyplus.net"
    exit 1
fi
echo "    ✅ Found: $EP_DIR"

# ── 2. Write .aria_env (project-local, gitignored) ─
echo ""
echo "[2] Writing .aria_env..."
echo "ENERGYPLUS_DIR=$EP_DIR" > .aria_env
echo "    ✅ .aria_env written"

# ── 3. Show available Small Office IDF files ───────
echo ""
echo "[3] Available Small Office IDF files:"
find "$EP_DIR/ExampleFiles" -iname "*OfficeSmall*" -o -iname "*SmallOffice*" 2>/dev/null | sort
echo ""
echo "    → Project uses: simulation/models/SmallOffice.idf"
if [ ! -f "simulation/models/SmallOffice.idf" ]; then
    echo "    ⚠️  SmallOffice.idf not found. Copy it:"
    echo "       cp \"$EP_DIR/ExampleFiles/RefBldgSmallOfficeNew2004_Chicago.idf\" simulation/models/SmallOffice.idf"
else
    echo "    ✅ SmallOffice.idf present"
fi

if [ ! -f "simulation/models/Chicago.epw" ]; then
    echo "    ⚠️  Chicago.epw not found. Copy it:"
    echo "       cp \"$EP_DIR/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw\" simulation/models/Chicago.epw"
else
    echo "    ✅ Chicago.epw present"
fi

# ── 4. Create / activate venv ──────────────────────
echo ""
echo "[4] Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "    ✅ .venv created"
else
    echo "    ✅ .venv already exists"
fi

source .venv/bin/activate

# ── 5. Install Python dependencies ────────────────
echo ""
echo "[5] Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "    ✅ Dependencies installed"

# ── 6. Verify pyenergyplus import ─────────────────
echo ""
echo "[6] Verifying pyenergyplus import..."
python3 -c "
import sys
sys.path.insert(0, '$EP_DIR')
from pyenergyplus.api import EnergyPlusAPI
print('    ✅ pyenergyplus OK')
"

# ── 7. Verify env_loader resolves correctly ────────
echo ""
echo "[7] Verifying env_loader.py..."
python3 -c "
import sys
sys.path.insert(0, '.')
from config.env_loader import get_ep_dir, load_settings
ep = get_ep_dir()
cfg = load_settings()
print(f'    ✅ get_ep_dir() → {ep}')
print(f'    ✅ load_settings() → model: {cfg[\"energyplus\"][\"model_path\"]}')
"

# ── 8. Check Ollama + model ────────────────────────
echo ""
echo "[8] Checking Ollama..."
if ! command -v ollama &>/dev/null; then
    echo "    ❌ Ollama not installed. Visit: https://ollama.com"
    exit 1
fi
echo "    ✅ Ollama found"

if ollama list 2>/dev/null | grep -q "qwen2.5:3b"; then
    echo "    ✅ qwen2.5:3b model present"
else
    echo "    Pulling qwen2.5:3b (2GB — takes a few minutes)..."
    ollama pull qwen2.5:3b
    echo "    ✅ qwen2.5:3b downloaded"
fi

# ── Done ───────────────────────────────────────────
echo ""
echo "=============================================="
echo " Setup complete!"
echo " Activate venv: source .venv/bin/activate"
echo " Next: bash scripts/run_baseline.sh"
echo "=============================================="
