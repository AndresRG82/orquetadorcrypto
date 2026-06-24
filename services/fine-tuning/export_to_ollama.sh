#!/bin/bash
# Export fine-tuned model to Ollama
set -e

GGUF_DIR="/app/models/finetuned/gguf"
OLLAMA_MODEL="qwen3-trader"

echo "=== Exporting to Ollama ==="

# Find the GGUF file
GGUF_FILE=$(find "$GGUF_DIR" -name "*.gguf" -type f | head -1)
if [ -z "$GGUF_FILE" ]; then
    echo "ERROR: No GGUF file found in $GGUF_DIR"
    exit 1
fi

echo "Found GGUF: $GGUF_FILE"

# Create Modelfile
cat > /tmp/Modelfile << EOF
FROM $GGUF_FILE

TEMPLATE """{{ if .System }}System: {{ .System }}

{{ end }}User: {{ .Prompt }}

Assistant: {{ .Response }}"""

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER repeat_penalty 1.1

SYSTEM """You are a crypto trading signal analyzer. Analyze the given signal and provide a clear assessment of whether it was profitable or losing, with insights on what went right or wrong. Be concise and specific about entry/exit prices, PnL, and lessons learned."""
EOF

echo "Created Modelfile"

# Create model in Ollama
echo "Creating Ollama model: $OLLAMA_MODEL"
ollama create "$OLLAMA_MODEL" -f /tmp/Modelfile

echo "Model created: $OLLAMA_MODEL"
echo ""
echo "To use: ollama run $OLLAMA_MODEL"
echo "To test: curl http://localhost:11434/api/generate -d '{\"model\": \"$OLLAMA_MODEL\", \"prompt\": \"Evaluate a trading signal: BTC/USDT buy at 65000\"}'"
