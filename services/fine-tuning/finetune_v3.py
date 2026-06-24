#!/usr/bin/env python3
"""Fine-tune Qwen2.5-3B using PEFT/LoRA without Unsloth (avoids triton kernel issues)."""
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
MAX_SEQ_LENGTH = 128
BATCH_SIZE = 1
GRAD_ACCUM = 2
LEARNING_RATE = 2e-4
NUM_EPOCHS = 2


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
    from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup
    from peft import LoraConfig, get_peft_model, TaskType
    
    print("=== Fine-tuning Qwen2.5-3B with PEFT/LoRA (no Unsloth) ===")
    
    device_name = torch.cuda.get_device_name(0)
    free_mem = torch.cuda.mem_get_info(0)[0] / 1e9
    print(f"GPU: {device_name}, Free VRAM: {free_mem:.1f}GB")
    
    train_raw, val_raw = load_data()
    
    print(f"\nLoading base model: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    
    from transformers import BitsAndBytesConfig
    
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb_config,
        device_map="cuda:0",
        trust_remote_code=True,
    )
    
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    
    print(f"Model loaded. Parameters: {sum(p.numel() for p in model.parameters()) / 1e6:.0f}M")
    
    lora_config = LoraConfig(
        r=LORA_RANK,
        lora_alpha=LORA_RANK * 2,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )
    
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    
    formatted_train = [format_prompt(ex) for ex in train_raw]
    formatted_val = [format_prompt(ex) for ex in val_raw]
    
    train_dataset = TradingDataset(formatted_train, tokenizer, MAX_SEQ_LENGTH)
    val_dataset = TradingDataset(formatted_val, tokenizer, MAX_SEQ_LENGTH) if formatted_val else None
    
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0) if val_dataset else None
    
    optimizer = torch.optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()), lr=LEARNING_RATE, weight_decay=0.01)
    total_steps = (len(train_loader) * NUM_EPOCHS) // GRAD_ACCUM
    scheduler = get_cosine_schedule_with_warmup(optimizer, num_warmup_steps=total_steps // 10, num_training_steps=total_steps)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print(f"\nStarting training: {NUM_EPOCHS} epochs, batch={BATCH_SIZE}, grad_accum={GRAD_ACCUM}")
    print(f"Effective batch size: {BATCH_SIZE * GRAD_ACCUM}")
    print(f"Training examples: {len(train_dataset)}")
    print(f"Total steps: {total_steps}")
    
    model.train()
    global_step = 0
    
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
                    vram = torch.cuda.memory_allocated() / 1e9
                    print(f"  Step {global_step}/{total_steps}, Loss: {avg_loss:.4f}, LR: {lr:.6f}, VRAM: {vram:.1f}GB")
        
        avg_loss = total_loss / num_batches
        print(f"Epoch {epoch+1}/{NUM_EPOCHS} - Avg Loss: {avg_loss:.4f}")
        
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
    
    print("\nMerging and exporting to GGUF...")
    merged_model = model.merge_and_unload()
    merged_path = os.path.join(OUTPUT_DIR, "merged")
    merged_model.save_pretrained(merged_path)
    tokenizer.save_pretrained(merged_path)
    print(f"Merged model saved to {merged_path}")
    
    print("\nFine-tuning complete!")


if __name__ == "__main__":
    finetune()
