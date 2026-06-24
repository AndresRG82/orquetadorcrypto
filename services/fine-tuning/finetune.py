#!/usr/bin/env python3
"""Fine-tune Qwen model using LoRA with 4-bit quantization for GTX 1660 SUPER."""
import os
import sys
import json
import torch
from datetime import datetime

sys.path.insert(0, "/app")

DATA_DIR = os.environ.get("FINETUNE_DATA_DIR", os.path.expanduser("~/Proyectos/crypto-trader/services/fine-tuning/data"))
OUTPUT_DIR = os.environ.get("FINETUNE_OUTPUT_DIR", os.path.expanduser("~/Proyectos/crypto-trader/services/fine-tuning/models"))
BASE_MODEL = "Qwen/Qwen2.5-3B"
LORA_RANK = 8
MAX_SEQ_LENGTH = 256
BATCH_SIZE = 1
GRAD_ACCUM = 4
LEARNING_RATE = 2e-4
NUM_EPOCHS = 2


def check_gpu():
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available")
    name = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"GPU: {name}, VRAM: {mem:.1f}GB")
    if mem < 5:
        raise RuntimeError(f"Need >=5GB VRAM, got {mem:.1f}GB")
    return name, mem


def load_data():
    train_path = os.path.join(DATA_DIR, "train.jsonl")
    val_path = os.path.join(DATA_DIR, "val.jsonl")
    if not os.path.exists(train_path):
        raise FileNotFoundError(f"Training data not found: {train_path}")
    
    train_data = []
    with open(train_path) as f:
        for line in f:
            train_data.append(json.loads(line))
    
    val_data = []
    if os.path.exists(val_path):
        with open(val_path) as f:
            for line in f:
                val_data.append(json.loads(line))
    
    print(f"Loaded {len(train_data)} train, {len(val_data)} val examples")
    return train_data, val_data


def format_prompt(example):
    """Format example into prompt for Qwen."""
    system = example.get("system", "")
    instruction = example.get("instruction", "")
    inp = example.get("input", "")
    
    prompt = ""
    if system:
        prompt += f"System: {system}\n\n"
    prompt += f"User: {instruction}"
    if inp:
        prompt += f"\n{inp}"
    prompt += "\n\nAssistant: "
    
    return {
        "prompt": prompt,
        "completion": example.get("output", ""),
    }


def finetune():
    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments
    from datasets import Dataset
    
    print("=== Fine-tuning Qwen3-4B with LoRA ===")
    check_gpu()
    
    train_data, val_data = load_data()
    
    print(f"\nLoading base model: {BASE_MODEL}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=torch.float16,
        load_in_4bit=False,
        device_map="auto",
    )
    
    print("Applying LoRA adapters...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_RANK,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=LORA_RANK * 2,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
    )
    
    formatted_train = [format_prompt(ex) for ex in train_data]
    formatted_val = [format_prompt(ex) for ex in val_data]
    
    # Save to disk first, then load (avoids pickling issues with Python 3.14)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        import json
        json.dump(formatted_train, f)
        train_path = f.name
    
    from datasets import load_dataset
    train_dataset = load_dataset('json', data_files=train_path, split='train')
    os.unlink(train_path)
    
    val_dataset = None
    if formatted_val:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(formatted_val, f)
            val_path = f.name
        val_dataset = load_dataset('json', data_files=val_path, split='train')
        os.unlink(val_path)
    
    def tokenize_function(examples):
        return tokenizer(
            examples["prompt"],
            truncation=True,
            max_length=MAX_SEQ_LENGTH,
            padding="max_length",
        )
    
    train_dataset = train_dataset.map(tokenize_function, batched=True)
    if val_dataset:
        val_dataset = val_dataset.map(tokenize_function, batched=True)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LEARNING_RATE,
        weight_decay=0.01,
        warmup_ratio=0.1,
        lr_scheduler_type="cosine",
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch" if val_dataset else "no",
        fp16=True,
        dataloader_num_workers=0,
        report_to="none",
        save_total_limit=2,
    )
    
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        args=training_args,
        max_seq_length=MAX_SEQ_LENGTH,
        packing=True,
    )
    
    print(f"\nStarting training: {NUM_EPOCHS} epochs, batch_size={BATCH_SIZE}, grad_accum={GRAD_ACCUM}")
    print(f"Effective batch size: {BATCH_SIZE * GRAD_ACCUM}")
    print(f"Training examples: {len(train_dataset)}")
    
    trainer.train()
    
    print("\nSaving LoRA adapter...")
    lora_path = os.path.join(OUTPUT_DIR, "lora_adapter")
    model.save_pretrained(lora_path)
    tokenizer.save_pretrained(lora_path)
    print(f"LoRA adapter saved to {lora_path}")
    
    print("\nExporting to GGUF (Q4_K_M)...")
    model.save_pretrained_gguf(
        os.path.join(OUTPUT_DIR, "gguf"),
        tokenizer,
        quantization_method="q4_k_m",
    )
    print(f"GGUF saved to {os.path.join(OUTPUT_DIR, 'gguf')}")
    
    print("\nFine-tuning complete!")


if __name__ == "__main__":
    finetune()
