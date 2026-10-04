import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import json
import random
import hashlib
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from models.autoencoder import PPGAutoencoder
from utils.spectral_loss import MultiScaleSpectralLoss
from utils.packet_codec import pack_payload, unpack_payload, quantize_latent, dequantize_latent

def seed_everything(seed: int = 2026):
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

def compute_file_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

class PPGContextDataset(Dataset):
    def __init__(self, records: list):
        self.records = records
        if records:
            norm_list = [np.stack([b["signal_norm"] for b in r["blocks"]], axis=0)[:, np.newaxis, :] for r in records]
            raw_list = [np.stack([b["signal_raw"] for b in r["blocks"]], axis=0).reshape(-1) for r in records]
            mu_list = [[b["mu"] for b in r["blocks"]] for r in records]
            std_list = [[b["s"] for b in r["blocks"]] for r in records]

            self.x_norm = torch.from_numpy(np.array(norm_list, dtype=np.float32))
            self.x_raw_seq = torch.from_numpy(np.array(raw_list, dtype=np.float32))
            self.mus = torch.from_numpy(np.array(mu_list, dtype=np.float32))
            self.stds = torch.from_numpy(np.array(std_list, dtype=np.float32))
        else:
            self.x_norm = torch.empty((0, 4, 1, 1000), dtype=torch.float32)
            self.x_raw_seq = torch.empty((0, 4000), dtype=torch.float32)
            self.mus = torch.empty((0, 4), dtype=torch.float32)
            self.stds = torch.empty((0, 4), dtype=torch.float32)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, idx):
        return {
            "x_norm": self.x_norm[idx],
            "x_raw_seq": self.x_raw_seq[idx],
            "mus": self.mus[idx],
            "stds": self.stds[idx],
            "pid": self.records[idx]["pid"],
            "hr_refs": [b["hr_ref"] for b in self.records[idx]["blocks"]],
            "rr_ref": self.records[idx]["rr_ref"]
        }

def collate_contexts(batch):
    b_size = len(batch)
    x_norm_all = torch.stack([item["x_norm"] for item in batch], dim=0).view(-1, 1, 1000)
    x_raw_seq_all = torch.stack([item["x_raw_seq"] for item in batch], dim=0)
    mus_all = torch.stack([item["mus"] for item in batch], dim=0).view(-1)
    stds_all = torch.stack([item["stds"] for item in batch], dim=0).view(-1)
    
    return {
        "x_norm": x_norm_all,
        "x_raw_seq": x_raw_seq_all,
        "mus": mus_all,
        "stds": stds_all,
        "batch_size": b_size
    }

def train_epoch(model, dataloader, optimizer, criterion, device):
    model.train()
    total_loss = 0.0
    total_time_loss = 0.0
    total_spec8_loss = 0.0
    total_spec32_loss = 0.0
    count = 0
    
    for batch in dataloader:
        x_norm = batch["x_norm"].to(device)
        x_raw_seq = batch["x_raw_seq"].to(device)
        mus = batch["mus"].to(device).view(-1, 1, 1)
        stds = batch["stds"].to(device).view(-1, 1, 1)
        b_size = batch["batch_size"]
        
        optimizer.zero_grad()
        x_hat_norm, z, scale_a = model(x_norm, use_ste=True)
        
        x_hat_raw_blocks = x_hat_norm * stds + mus
        x_hat_raw_seq = x_hat_raw_blocks.view(b_size, 4000)
        x_raw_blocks = x_raw_seq.reshape(-1, 1, 1000)
        
        loss, loss_dict = criterion(x_raw_blocks, x_hat_raw_blocks, x_raw_seq, x_hat_raw_seq, block_scales=stds)
        loss.backward()
        optimizer.step()
        
        total_loss += loss.item() * b_size
        total_time_loss += loss_dict["loss_time"] * b_size
        total_spec8_loss += loss_dict["loss_spec_8s"] * b_size
        total_spec32_loss += loss_dict["loss_spec_32s"] * b_size
        count += b_size
        
    if count == 0:
        raise ValueError("Tập huấn luyện rỗng; không thể báo mất mát bằng 0")
    return {
        "loss": total_loss / count if count > 0 else 0.0,
        "loss_time": total_time_loss / count if count > 0 else 0.0,
        "loss_spec8": total_spec8_loss / count if count > 0 else 0.0,
        "loss_spec32": total_spec32_loss / count if count > 0 else 0.0
    }

def evaluate_loss(model, dataloader, criterion, device):
    model.eval()
    total_loss = 0.0
    count = 0
    
    with torch.no_grad():
        for batch in dataloader:
            x_norm = batch["x_norm"].to(device)
            x_raw_seq = batch["x_raw_seq"].to(device)
            mus = batch["mus"].to(device).view(-1, 1, 1)
            stds = batch["stds"].to(device).view(-1, 1, 1)
            b_size = batch["batch_size"]
            
            z, _ = model.encoder(x_norm)
            decoded_latents, decoded_mus, decoded_stds = [], [], []
            for index, latent in enumerate(z.cpu().numpy()):
                q, a = quantize_latent(latent)
                config_id = 1 if model.encoder.lz == 115 else 2 if model.encoder.lz == 52 else 0
                packet = pack_payload(1, config_id, index, float(mus[index].item()),
                                      float(stds[index].item()), a, q)
                decoded = unpack_payload(packet, expected_lz=model.encoder.lz,
                                         expected_config_id=config_id)
                if not decoded["valid"]:
                    raise ValueError(f"Validation codec failed: {decoded['error_msg']}")
                decoded_latents.append(dequantize_latent(decoded["payload_int16"], decoded["a"]))
                decoded_mus.append(decoded["mu"])
                decoded_stds.append(decoded["s"])
            z_bytes = torch.as_tensor(np.stack(decoded_latents), dtype=torch.float32, device=device)
            x_hat_norm = model.decoder(z_bytes)
            mus = torch.tensor(decoded_mus, dtype=torch.float32, device=device).view(-1, 1, 1)
            stds = torch.tensor(decoded_stds, dtype=torch.float32, device=device).view(-1, 1, 1)
            x_hat_raw_blocks = x_hat_norm * stds + mus
            x_hat_raw_seq = x_hat_raw_blocks.view(b_size, 4000)
            x_raw_blocks = x_raw_seq.reshape(-1, 1, 1000)
            
            loss, _ = criterion(x_raw_blocks, x_hat_raw_blocks, x_raw_seq, x_hat_raw_seq, block_scales=stds)
            total_loss += loss.item() * b_size
            count += b_size
            
    if count == 0:
        raise ValueError("Tập kiểm định rỗng; không thể báo mất mát bằng 0")
    return total_loss / count

def train_fold(train_records, val_records, lz=115, beta=0.5, 
               hr_weight=2.0, rr_weight=4.0, max_epochs=30, 
               batch_size=16, lr=0.001, patience=15, lr_patience=5,
               lr_factor=0.5, fs=125, hr_band=(0.8, 3.0), rr_band=(0.1, 0.4),
               weight_decay=0.0001,
               device='cpu', seed=2026, save_checkpoint=None,
               checkpoint_metadata=None, eps_f=1e-8):
    seed_everything(seed)
    train_dataset = PPGContextDataset(train_records)
    val_dataset = PPGContextDataset(val_records)
    
    g = torch.Generator()
    g.manual_seed(seed)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, collate_fn=collate_contexts, generator=g)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, collate_fn=collate_contexts)
    
    model = PPGAutoencoder(lz=lz).to(device)
    criterion = MultiScaleSpectralLoss(
        fs=fs, beta=beta, hr_band=tuple(hr_band), hr_weight=hr_weight,
        rr_band=tuple(rr_band), rr_weight=rr_weight, eps_f=eps_f
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=lr_factor, patience=lr_patience
    )
    
    best_loss = float('inf')
    best_epoch = 0
    patience_counter = 0
    history = []
    
    for epoch in range(1, max_epochs + 1):
        tr_res = train_epoch(model, train_loader, optimizer, criterion, device)
        val_loss = evaluate_loss(model, val_loader, criterion, device)
        scheduler.step(val_loss)
        
        cur_lr = optimizer.param_groups[0]["lr"]
        history.append({
            "epoch": epoch,
            "train_loss": tr_res["loss"],
            "val_loss": val_loss,
            "lr": cur_lr
        })
        
        if val_loss < best_loss:
            best_loss = val_loss
            best_epoch = epoch
            patience_counter = 0
            print(f"    Epoch {epoch:3d}/{max_epochs}: Train Loss = {tr_res['loss']:.5f}, Val Loss = {val_loss:.5f} [NEW BEST], LR = {cur_lr:.6f}", flush=True)
            if save_checkpoint:
                ckpt_dir = os.path.dirname(save_checkpoint)
                if ckpt_dir:
                    os.makedirs(ckpt_dir, exist_ok=True)
                checkpoint = {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "best_loss": best_loss,
                    "lz": lz,
                    "beta": beta,
                    "hr_weight": hr_weight,
                    "rr_weight": rr_weight,
                    "seed": seed,
                    "history": history.copy()
                }
                checkpoint.update(checkpoint_metadata or {})
                torch.save(checkpoint, save_checkpoint)
        else:
            patience_counter += 1
            if epoch % 5 == 0 or patience_counter >= patience:
                print(f"    Epoch {epoch:3d}/{max_epochs}: Train Loss = {tr_res['loss']:.5f}, Val Loss = {val_loss:.5f} (Best: {best_loss:.5f}), LR = {cur_lr:.6f}", flush=True)
            if patience_counter >= patience:
                print(f"    -> Early stopping triggered at epoch {epoch} (patience={patience})", flush=True)
                break

    if save_checkpoint and os.path.exists(save_checkpoint):
        checkpoint = torch.load(save_checkpoint, map_location="cpu", weights_only=False)
        checkpoint["training_complete"] = True
        checkpoint["epochs_ran"] = len(history)
        checkpoint["stop_reason"] = "early_stopping" if len(history) < max_epochs else "max_epochs"
        checkpoint["history"] = history
        torch.save(checkpoint, save_checkpoint)

    return model, best_epoch, best_loss, history

def train_final_full_dev(dev_records, lz=115, beta=0.5, 
                         hr_weight=2.0, rr_weight=4.0, target_epochs=20, 
                         batch_size=16, lr=0.001, lr_patience=5,
                         fs=125, hr_band=(0.8, 3.0), rr_band=(0.1, 0.4),
                         weight_decay=0.0001,
                         device='cpu', seed=2026, save_checkpoint=None,
                         checkpoint_metadata=None, eps_f=1e-8):
    seed_everything(seed)
    dev_dataset = PPGContextDataset(dev_records)
    
    g = torch.Generator()
    g.manual_seed(seed)
    dev_loader = DataLoader(dev_dataset, batch_size=batch_size, shuffle=True, collate_fn=collate_contexts, generator=g)
    
    model = PPGAutoencoder(lz=lz).to(device)
    criterion = MultiScaleSpectralLoss(
        fs=fs, beta=beta, hr_band=tuple(hr_band), hr_weight=hr_weight,
        rr_band=tuple(rr_band), rr_weight=rr_weight, eps_f=eps_f
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    
    history = []
    for epoch in range(1, target_epochs + 1):
        tr_res = train_epoch(model, dev_loader, optimizer, criterion, device)
        cur_lr = optimizer.param_groups[0]["lr"]
        history.append({
            "epoch": epoch,
            "train_loss": tr_res["loss"],
            "lr": cur_lr
        })
        if epoch % 5 == 0 or epoch == 1 or epoch == target_epochs:
            print(f"    Final Epoch {epoch:3d}/{target_epochs}: Train Loss = {tr_res['loss']:.5f}, LR = {cur_lr:.6f}", flush=True)
        
    if save_checkpoint:
        ckpt_dir = os.path.dirname(save_checkpoint)
        if ckpt_dir:
            os.makedirs(ckpt_dir, exist_ok=True)
        checkpoint = {
            "epoch": target_epochs,
            "model_state_dict": model.state_dict(),
            "final_train_loss": history[-1]["train_loss"],
            "lz": lz,
            "beta": beta,
            "hr_weight": hr_weight,
            "rr_weight": rr_weight,
            "seed": seed,
            "trained_on_all_dev": True,
            "history": history
        }
        checkpoint.update(checkpoint_metadata or {})
        torch.save(checkpoint, save_checkpoint)
        
    return model, history
