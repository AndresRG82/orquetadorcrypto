#!/usr/bin/env python3
"""Monitor entrenamiento en tiempo real. Uso: python3 monitor.py"""
import os, time, json, subprocess, shutil
from datetime import datetime

LOG = os.path.expanduser("~/Proyectos/crypto-trader/services/fine-tuning/training.log")
PID = 2920481

def get_gpu():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        parts = r.stdout.strip().split(", ")
        return {"temp": parts[0], "util": parts[1], "mem_used": parts[2], "mem_total": parts[3]}
    except:
        return None

def get_proc():
    try:
        r = subprocess.run(["ps", "-p", str(PID), "-o", "etimes,rss", "--no-headers"],
                           capture_output=True, text=True, timeout=3)
        parts = r.stdout.strip().split()
        return int(parts[0]), int(parts[1]) // 1024
    except:
        return None, None

def parse_log(lines):
    step = total = loss = val_loss = lr = pct = eta = None
    for line in reversed(lines):
        if not step and "Step " in line and "/" in line:
            import re
            m = re.search(r"Step (\d+)/(\d+).*Loss: ([\d.]+).*LR: ([\d.]+)", line)
            if m:
                step, total, loss, lr = int(m.group(1)), int(m.group(2)), float(m.group(3)), float(m.group(4))
                pct = step / total * 100
        if not val_loss and "VAL Step" in line:
            m = re.search(r"Loss: ([\d.]+)", line)
            if m:
                val_loss = float(m.group(1))
        if step and val_loss is not None and lr is not None:
            break
    return step, total, loss, val_loss, lr, pct

def draw_bar(pct, width=30):
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)

def clear():
    print("\033[2J\033[H", end="")

def main():
    print(f"Monitor de entrenamiento - PID {PID}\n")
    while True:
        try:
            with open(LOG) as f:
                lines = f.readlines()
        except:
            lines = []

        step, total, loss, val_loss, lr, pct = parse_log(lines)
        gpu = get_gpu()
        etime, rss = get_proc()

        clear()

        print(f"{'='*55}")
        print(f"  ENTRENAMIENTO CRYPTO-TRADER")
        print(f"{'='*55}")

        if step and total:
            h = etime // 3600 if etime else 0
            m2 = (etime % 3600) // 60
            elapsed_str = f"{h}h {m2}m" if etime else "?"
            print(f"\n  Progreso:   {step:>5} / {total} ({pct:.1f}%)")
            print(f"  {draw_bar(pct)}")
            print(f"  Elapsed:    {elapsed_str}")
            if step > 0:
                remaining_pct = 100 - pct
                rate = etime / step if etime else 0
                eta_s = (total - step) * rate
                eta_h = eta_s / 3600
                print(f"  ETA:        ~{eta_h:.1f}h")
            print(f"\n  Loss:       {loss:.4f}" if loss else "")
            if val_loss is not None:
                print(f"  Val Loss:   {val_loss:.4f}")
            print(f"  LR:         {lr:.6f}" if lr else "")
        else:
            print("\n  Esperando datos del entrenamiento...")

        if gpu:
            print(f"\n{'─'*55}")
            print(f"  GPU:        {gpu['temp']}°C | {gpu['util']}% | "
                  f"{gpu['mem_used']}/{gpu['mem_total']} MB")
        if rss:
            print(f"  RAM proc:   {rss} MB")

        dt = datetime.now().strftime("%H:%M:%S")
        print(f"\n{'─'*55}")
        print(f"  Última actualización: {dt}")
        print(f"{'='*55}")

        time.sleep(2)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Monitor detenido.")
