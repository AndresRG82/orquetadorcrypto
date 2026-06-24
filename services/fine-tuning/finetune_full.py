#!/usr/bin/env python3
"""Full fine-tuning run - Qwen2.5-3B with PEFT/LoRA on all training data."""
import sys, os, json, torch
from torch.utils.data import Dataset, DataLoader
from datetime import datetime

DATA_DIR = os.path.expanduser("~/Proyectos/crypto-trader/services/fine-tuning/data")
OUTPUT_DIR = os.path.expanduser("~/Proyectos/crypto-trader/services/fine-tuning/models")

MAX_SEQ_LENGTH = 128
BATCH_SIZE = 1
GRAD_ACCUM = 2
LEARNING_RATE = 2e-4
NUM_EPOCHS = 2
LOG_EVERY = 100
VAL_EVERY = 1000

class TradingDataset(Dataset):
    def __init__(self, data, tok, ml):
        self.data = data
        self.tok = tok
        self.ml = ml
    def __len__(self):
        return len(self.data)
    def __getitem__(self, idx):
        ex = self.data[idx]
        p = "User: " + ex.get("prompt", "") + "\n\nAssistant: "
        c = ex.get("output", "")
        full = p + c
        enc = self.tok(full, truncation=True, max_length=self.ml, padding="max_length", return_tensors="pt")
        ids = enc["input_ids"].squeeze()
        mask = enc["attention_mask"].squeeze()
        plen = self.tok(p, truncation=True, max_length=self.ml, return_tensors="pt")["input_ids"].shape[1]
        labels = ids.clone()
        labels[:plen] = -100
        labels[mask == 0] = -100
        return {"input_ids": ids, "attention_mask": mask, "labels": labels}

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
    return {"prompt": prompt, "output": example.get("output", "")}

def main():
    start_time = datetime.now()
    print(f"=== Fine-tuning started at {start_time.isoformat()} ===", flush=True)
    
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, TaskType
    
    # Load data
    train_raw = []
    with open(os.path.join(DATA_DIR, "train.jsonl")) as f:
        for line in f:
            train_raw.append(json.loads(line))
    
    val_raw = []
    val_path = os.path.join(DATA_DIR, "val.jsonl")
    if os.path.exists(val_path):
        with open(val_path) as f:
            for line in f:
                val_raw.append(json.loads(line))
    
    train_data = [format_prompt(ex) for ex in train_raw]
    val_data = [format_prompt(ex) for ex in val_raw]
    print(f"Data: {len(train_data)} train, {len(val_data)} val", flush=True)
    
    # Load model
    print("Loading Qwen2.5-3B with 4-bit quantization...", flush=True)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        "Qwen/Qwen2.5-3B", quantization_config=bnb_config, device_map="cuda:0"
    )
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    
    lora_config = LoraConfig(
        r=8, lora_alpha=16,
        target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
        lora_dropout=0.05, bias="none", task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-3B")
    tokenizer.pad_token = tokenizer.eos_token
    
    # Create datasets
    train_ds = TradingDataset(train_data, tokenizer, MAX_SEQ_LENGTH)
    val_ds = TradingDataset(val_data, tokenizer, MAX_SEQ_LENGTH) if val_data else None
    train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, num_workers=0)
    val_dl = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=0) if val_ds else None
    
    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=LEARNING_RATE, weight_decay=0.01
    )
    total_steps = (len(train_dl) * NUM_EPOCHS) // GRAD_ACCUM
    from transformers import get_cosine_schedule_with_warmup
    scheduler = get_cosine_schedule_with_warmup(
        optimizer, num_warmup_steps=total_steps // 10, num_training_steps=total_steps
    )
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Training loop
    print(f"\nTraining: {NUM_EPOCHS} epochs, batch={BATCH_SIZE}, grad_accum={GRAD_ACCUM}", flush=True)
    print(f"Effective batch: {BATCH_SIZE * GRAD_ACCUM}, Steps: {total_steps}", flush=True)
    print(flush=True)
    
    model.train()
    global_step = 0
    losses = []
    
    for epoch in range(NUM_EPOCHS):
        epoch_loss = 0
        epoch_batches = 0
        
        for batch_idx, batch in enumerate(train_dl):
            ids = batch["input_ids"].to("cuda:0")
            mask = batch["attention_mask"].to("cuda:0")
            labels = batch["labels"].to("cuda:0")
            
            out = model(input_ids=ids, attention_mask=mask, labels=labels)
            loss = out.loss / GRAD_ACCUM
            loss.backward()
            epoch_loss += out.loss.item()
            epoch_batches += 1
            losses.append(out.loss.item())
            
            if (batch_idx + 1) % GRAD_ACCUM == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                global_step += 1
                
                if global_step % LOG_EVERY == 0:
                    avg = sum(losses[-LOG_EVERY:]) / len(losses[-LOG_EVERY:])
                    lr = scheduler.get_last_lr()[0]
                    vram = torch.cuda.memory_allocated() / 1e9
                    elapsed = (datetime.now() - start_time).total_seconds()
                    print(f"  Step {global_step}/{total_steps} | Loss: {avg:.4f} | LR: {lr:.6f} | VRAM: {vram:.1f}GB | {elapsed:.0f}s", flush=True)
                
                if global_step % VAL_EVERY == 0 and val_dl:
                    model.eval()
                    val_loss = 0
                    val_count = 0
                    with torch.no_grad():
                        for vb in val_dl:
                            vids = vb["input_ids"].to("cuda:0")
                            vmask = vb["attention_mask"].to("cuda:0")
                            vlabels = vb["labels"].to("cuda:0")
                            vout = model(input_ids=vids, attention_mask=vmask, labels=vlabels)
                            val_loss += vout.loss.item()
                            val_count += 1
                    avg_val = val_loss / val_count
                    print(f"  VAL Step {global_step} | Loss: {avg_val:.4f}", flush=True)
                    model.train()
        
        avg_epoch = epoch_loss / epoch_batches
        print(f"\nEpoch {epoch+1}/{NUM_EPOCHS} done. Avg Loss: {avg_epoch:.4f}", flush=True)
        
        # Save checkpoint
        ckpt_path = os.path.join(OUTPUT_DIR, f"checkpoint_epoch{epoch+1}")
        model.save_pretrained(ckpt_path)
        tokenizer.save_pretrained(ckpt_path)
        print(f"  Checkpoint saved: {ckpt_path}", flush=True)
    
    # Final save
    print("\nSaving final LoRA adapter...", flush=True)
    final_path = os.path.join(OUTPUT_DIR, "lora_adapter")
    model.save_pretrained(final_path)
    tokenizer.save_pretrained(final_path)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n=== DONE in {elapsed/60:.1f} minutes ===", flush=True)
    print(f"Adapter: {final_path}", flush=True)
    print(f"Loss curve: {losses[0]:.4f} -> {losses[-1]:.4f}", flush=True)
    
    # Save training stats
    stats = {
        "model": "Qwen/Qwen2.5-3B",
        "lora_rank": 8,
        "epochs": NUM_EPOCHS,
        "examples": len(train_data),
        "total_steps": global_step,
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "duration_minutes": elapsed / 60,
        "timestamp": datetime.now().isoformat(),
    }
    with open(os.path.join(OUTPUT_DIR, "training_stats.json"), "w") as f:
        json.dump(stats, f, indent=2)

if __name__ == "__main__":
    main()
