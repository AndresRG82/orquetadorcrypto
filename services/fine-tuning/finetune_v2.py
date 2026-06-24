#!/usr/bin/env python3
"""Fine-tune Qwen2.5-3B using LoRA with pure PyTorch (no datasets lib - Python 3.14 compat)."""
import os
import sys
import json
import torch
from torch.utils.data import Dataset, DataLoader
from datetime import datetime

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
    free = torch.cuda.mem_get_info(0)[0] / 1e9
    print(f"GPU: {name}, Total: {mem:.1f}GB, Free: {free:.1f}GB")
    return name, mem


def load_data():
    train_path = os.path.join(DATA_DIR, "train.jsonl")
    val_path = os.path.join(DATA_DIR, "val.jsonl")
    
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


class TradingDataset(Dataset):
    def __init__(self, data, tokenizer, max_length):
        self.data = data
        self.tokenizer = tokenizer
        self.max_length = max_length
    
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        ex = self.data[idx]
        prompt = ex.get("prompt", "")
        completion = ex.get("output", "")
        
        full_text = prompt + completion
        encoded = self.tokenizer(
            full_text,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        
        input_ids = encoded["input_ids"].squeeze()
        attention_mask = encoded["attention_mask"].squeeze()
        
        # Create labels: mask prompt tokens with -100
        prompt_encoded = self.tokenizer(
            prompt,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        prompt_len = prompt_encoded["input_ids"].shape[1]
        labels = input_ids.clone()
        labels[:prompt_len] = -100
        labels[attention_mask == 0] = -100
        
        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }


def format_prompt(example):
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
    from transformers import get_cosine_schedule_with_warmup
    
    print("=== Fine-tuning Qwen2.5-3B with LoRA (pure PyTorch) ===")
    check_gpu()
    
    train_raw, val_raw = load_data()
    
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
    
    formatted_train = [format_prompt(ex) for ex in train_raw]
    formatted_val = [format_prompt(ex) for ex in val_raw]
    
    train_dataset = TradingDataset(formatted_train, tokenizer, MAX_SEQ_LENGTH)
    val_dataset = TradingDataset(formatted_val, tokenizer, MAX_SEQ_LENGTH) if formatted_val else None
    
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0) if val_dataset else None
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=0.01)
    total_steps = len(train_loader) * NUM_EPOCHS // GRAD_ACCUM
    scheduler = get_cosine_schedule_with_warmup(optimizer, num_warmup_steps=total_steps // 10, num_training_steps=total_steps)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print(f"\nStarting training: {NUM_EPOCHS} epochs, batch_size={BATCH_SIZE}, grad_accum={GRAD_ACCUM}")
    print(f"Effective batch size: {BATCH_SIZE * GRAD_ACCUM}")
    print(f"Training examples: {len(train_dataset)}")
    print(f"Total steps: {total_steps}")
    
    model.train()
    global_step = 0
    epoch_losses = []
    
    for epoch in range(NUM_EPOCHS):
        total_loss = 0
        num_batches = 0
        
        for batch_idx, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(model.device)
            attention_mask = batch["attention_mask"].to(model.device)
            labels = batch["labels"].to(model.device)
            
            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = outputs.loss / GRAD_ACCUM
            loss.backward()
            total_loss += outputs.loss.item()
            num_batches += 1
            
            if (batch_idx + 1) % GRAD_ACCUM == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                global_step += 1
                
                if global_step % 50 == 0:
                    avg_loss = total_loss / num_batches
                    lr = scheduler.get_last_lr()[0]
                    print(f"  Step {global_step}/{total_steps}, Loss: {avg_loss:.4f}, LR: {lr:.6f}")
        
        avg_loss = total_loss / num_batches
        epoch_losses.append(avg_loss)
        print(f"Epoch {epoch+1}/{NUM_EPOCHS} - Avg Loss: {avg_loss:.4f}")
        
        # Validation
        if val_loader:
            model.eval()
            val_loss = 0
            val_batches = 0
            with torch.no_grad():
                for batch in val_loader:
                    input_ids = batch["input_ids"].to(model.device)
                    attention_mask = batch["attention_mask"].to(model.device)
                    labels = batch["labels"].to(model.device)
                    outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                    val_loss += outputs.loss.item()
                    val_batches += 1
            avg_val_loss = val_loss / val_batches
            print(f"  Val Loss: {avg_val_loss:.4f}")
            model.train()
    
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
    
    print(f"\nFine-tuning complete! Losses: {epoch_losses}")


if __name__ == "__main__":
    finetune()
