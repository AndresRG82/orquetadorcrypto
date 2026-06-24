#!/usr/bin/env python3
"""Convert training data from training-export to Alpaca format for fine-tuning."""
import json
import sys
import os
from datetime import datetime

INPUT_DIR = os.environ.get("TRAINING_DATA_DIR", "/app/data/training")
OUTPUT_DIR = os.environ.get("FINETUNE_DATA_DIR", os.path.expanduser("~/Proyectos/crypto-trader/services/fine-tuning/data"))


def load_training_data(input_path: str) -> list[dict]:
    with open(input_path, "r") as f:
        return json.load(f)


def convert_to_alpaca(examples: list[dict]) -> list[dict]:
    """Convert instruction/input/output to Alpaca format with system prompt."""
    system_prompt = (
        "You are a crypto trading signal analyzer. "
        "Analyze the given signal and provide a clear assessment of whether it was "
        "profitable or losing, with insights on what went right or wrong. "
        "Be concise and specific about entry/exit prices, PnL, and lessons learned."
    )
    alpaca_data = []
    for ex in examples:
        instruction = ex.get("instruction", "")
        inp = ex.get("input", "")
        output = ex.get("output", "")
        if not instruction or not output:
            continue
        alpaca_data.append({
            "instruction": instruction,
            "input": inp,
            "output": output,
            "system": system_prompt,
            "metadata": {
                "source": ex.get("source", ""),
                "strategy": ex.get("strategy", ""),
                "symbol": ex.get("symbol", ""),
            }
        })
    return alpaca_data


def split_data(data: list[dict], train_ratio: float = 0.9) -> tuple[list, list]:
    """Split data into train and validation sets."""
    split_idx = int(len(data) * train_ratio)
    return data[:split_idx], data[split_idx:]


def save_jsonl(data: list[dict], path: str):
    with open(path, "w") as f:
        for item in data:
            f.write(json.dumps(item) + "\n")


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(INPUT_DIR, "latest.json")
    print(f"Loading training data from {input_path}...")
    
    raw_data = load_training_data(input_path)
    print(f"Loaded {len(raw_data)} examples")
    
    alpaca_data = convert_to_alpaca(raw_data)
    print(f"Converted {len(alpaca_data)} examples to Alpaca format")
    
    train_data, val_data = split_data(alpaca_data)
    print(f"Split: {len(train_data)} train, {len(val_data)} validation")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    train_path = os.path.join(OUTPUT_DIR, f"train_{timestamp}.jsonl")
    val_path = os.path.join(OUTPUT_DIR, f"val_{timestamp}.jsonl")
    latest_train = os.path.join(OUTPUT_DIR, "train.jsonl")
    latest_val = os.path.join(OUTPUT_DIR, "val.jsonl")
    
    save_jsonl(train_data, train_path)
    save_jsonl(train_data, latest_train)
    save_jsonl(val_data, val_path)
    save_jsonl(val_data, latest_val)
    
    print(f"Saved train: {train_path}")
    print(f"Saved val: {val_path}")
    
    stats = {
        "total": len(alpaca_data),
        "train": len(train_data),
        "val": len(val_data),
        "profitable": sum(1 for e in alpaca_data if "profitable" in e.get("output", "").lower() or "correct" in e.get("output", "").lower()),
        "losing": sum(1 for e in alpaca_data if "losing" in e.get("output", "").lower() or "incorrect" in e.get("output", "").lower()),
        "strategies": list(set(e["metadata"]["strategy"] for e in alpaca_data if e["metadata"]["strategy"])),
        "timestamp": datetime.now().isoformat(),
    }
    print(f"\nStats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
