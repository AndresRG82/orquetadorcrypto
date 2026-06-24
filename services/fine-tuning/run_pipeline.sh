#!/bin/bash
# Run the complete fine-tuning pipeline
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
DATA_DIR="/app/data/training"

echo "=== Crypto Trader Fine-tuning Pipeline ==="
echo ""

# Step 1: Convert training data
echo "[1/3] Converting training data to Alpaca format..."
source "$VENV/bin/activate"
python3 "$SCRIPT_DIR/convert_data.py" "$DATA_DIR/latest.json"
echo ""

# Step 2: Fine-tune
echo "[2/3] Fine-tuning Qwen3-4B with LoRA..."
echo "WARNING: This will take 1-3 hours depending on GPU load"
echo "Make sure no other GPU-heavy processes are running"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted"
    exit 1
fi

python3 "$SCRIPT_DIR/finetune.py"
echo ""

# Step 3: Export to Ollama
echo "[3/3] Exporting to Ollama..."
bash "$SCRIPT_DIR/export_to_ollama.sh"
echo ""

echo "=== Fine-tuning pipeline complete ==="
echo "Model: qwen3-trader"
echo "Usage: ollama run qwen3-trader"
