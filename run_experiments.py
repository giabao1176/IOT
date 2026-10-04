import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import json
import time
import hashlib
import logging
import argparse
import numpy as np
import yaml
import torch

from train import (
    seed_everything, compute_file_sha256, train_fold, train_final_full_dev
)
from evaluate import evaluate_all_methods_on_test_set
from export_encoder import export_encoder_model
from benchmark_edge import run_edge_benchmark
from models.autoencoder import PPGAutoencoder
from utils.artifact_integrity import validate_saved_checkpoint, strict_json, stage_fingerprint, validate_fixed_configuration

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ALL_CONFIGS = [
    ("De xuat Day du (8x)",           "best_model_8x.pt",              115, 0.5, 2.0, 4.0),
    ("De xuat Day du (16x)",          "best_model_16x.pt",             52,  0.5, 2.0, 4.0),
    ("Autoencoder-MSE (8x)",          "baseline_ae_mse_8x.pt",         115, 0.0, 1.0, 1.0),
    ("Autoencoder-MSE (16x)",         "baseline_ae_mse_16x.pt",        52,  0.0, 1.0, 1.0),
    ("Boc tach B: Pho deu (8x)",     "ablation_unweighted_8x.pt",      115, 0.5, 1.0, 1.0),
    ("Boc tach C: Uu tien HR (8x)",  "ablation_hr_only_8x.pt",         115, 0.5, 2.0, 1.0),
]

def setup_logger(log_file: str):
    logger = logging.getLogger("C5_Pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    return logger


def compute_protocol_hash(cfg: dict) -> str:
    raw = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_source_code_hash() -> str:
    source_files = [
        "benchmark_edge.py",
        "evaluate.py",
        "models/autoencoder.py",
        "models/dct_baseline.py",
        "prepare_data.py",
        "run_experiments.py",
        "train.py",
        "utils/estimators.py",
        "utils/packet_codec.py",
        "utils/spectral_loss.py"
        ,"utils/artifact_integrity.py", "utils/deployment_reference.py", "export_encoder.py"
    ]
    h = hashlib.sha256()
    for rel in sorted(source_files):
        p = os.path.join(BASE_DIR, rel)
        if os.path.exists(p):
            h.update(rel.encode('utf-8'))
            h.update(compute_file_sha256(p).encode('utf-8'))
    return h.hexdigest()


def checkpoint_metadata(cfg: dict, protocol_hash: str, splits_path: str,
                        data_path: str, config_name: str) -> dict:
    return {
        "protocol_hash": protocol_hash,
        "config_version": cfg["project"]["version"],
        "config_name": config_name,
        "splits_sha256": compute_file_sha256(splits_path),
        "processed_data_sha256": compute_file_sha256(data_path),
        "source_code_hash": compute_source_code_hash(),
        "max_epochs": int(cfg["training"]["max_epochs"]),
        "lr_patience": int(cfg["training"]["lr_patience"]),
        "early_stop_patience": int(cfg["training"]["early_stop_patience"]),
    }


def checkpoint_matches(ckpt: dict, expected: dict, expected_epoch: int = None) -> bool:
    if any(ckpt.get(k) != v for k, v in expected.items()):
        return False
    return expected_epoch is None or int(ckpt.get("epoch", -1)) == int(expected_epoch)


def stage_prepare(cfg: dict, logger: logging.Logger):
    validate_fixed_configuration(cfg)
    logger.info("=== GIAI DOAN 1: CHUAN BI DU LIEU VA KIEM TRA DATASET ===")
    data_path = os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy")
    splits_path = os.path.join(BASE_DIR, cfg["data"]["splits_file"])

    if not os.path.exists(data_path) or not os.path.exists(splits_path):
        logger.info("Chua co du lieu processed, goi prepare_data.py...")
        from prepare_data import process_all_bidmc
        process_all_bidmc(config=cfg)

    data = np.load(data_path, allow_pickle=True)
    with open(splits_path, 'r', encoding='utf-8') as f:
        splits = json.load(f)

    test_pids = set(splits.get("test_records", splits.get("test_pids")))
    dev_pids  = set(splits.get("dev_records", splits.get("dev_pids")))
    test_subs = set(splits.get("test_subjects", []))
    dev_subs  = set(splits.get("dev_subjects", []))

    dev_records  = [r for r in data if r["pid"] in dev_pids]
    test_records = [r for r in data if r["pid"] in test_pids]

    logger.info(f"Tong so ngu canh 32s: {len(data)}")
    logger.info(f" - Tap phat trien ({len(dev_subs)} doi tuong, {len(dev_pids)} ban ghi): {len(dev_records)} ngu canh")
    logger.info(f" - Tap kiem thu khoa ({len(test_subs)} doi tuong, {len(test_pids)} ban ghi): {len(test_records)} ngu canh")

    return data, splits


def stage_cross_validate(cfg: dict, dev_records: list, splits: dict, device: str,
                         protocol_hash: str, logger: logging.Logger,
                         resume: bool = False, selected_configs=None,
                         output_path: str = None):
    logger.info("=== GIAI DOAN 2: KIEM DINH CHEO 5 NEP RIENG CHO 6 CAU HINH HOC MAY ===")
    ckpt_dir = os.path.join(BASE_DIR, "checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)

    folds          = splits["folds"]
    batch_size     = cfg["training"]["batch_contexts"]
    max_epochs     = cfg["training"]["max_epochs"]
    lr             = cfg["training"]["learning_rate"]
    lr_patience    = cfg["training"]["lr_patience"]
    early_patience = cfg["training"]["early_stop_patience"]
    lr_factor      = cfg["training"]["lr_factor"]
    fs             = cfg["data"]["sampling_rate"]
    hr_band        = cfg["training"]["loss"]["hr_band"]
    rr_band        = cfg["training"]["loss"]["rr_band"]
    weight_decay   = cfg["training"]["weight_decay"]
    seed           = cfg["project"]["seed"]
    splits_path = os.path.join(BASE_DIR, cfg["data"]["splits_file"])
    data_path = os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy")

    cv_per_config = {}

    configs_to_run = selected_configs if selected_configs is not None else ALL_CONFIGS
    for cfg_name, filename, lz, beta, hr_w, rr_w in configs_to_run:
        logger.info(f"\n--- Kiem dinh cheo cho cau hinh: {cfg_name} ---")
        fold_best_epochs = []
        fold_val_losses  = []
        fold_details     = []

        for f_idx in range(1, 6):
            fold_key = f"fold_{f_idx}"
            tr_pids  = set(folds[fold_key]["train_pids"])
            val_pids = set(folds[fold_key]["val_pids"])

            tr_recs  = [r for r in dev_records if r["pid"] in tr_pids]
            val_recs = [r for r in dev_records if r["pid"] in val_pids]

            safe_name = (cfg_name.replace(" ", "_").replace(":", "")
                                 .replace("(", "").replace(")", "").replace("/", "_"))
            ckpt_f = os.path.join(ckpt_dir, f"cv_{safe_name}_fold_{f_idx}.pt")
            meta = checkpoint_metadata(cfg, protocol_hash, splits_path, data_path, cfg_name)
            meta.update({"fold": f_idx, "train_pids": sorted(tr_pids), "val_pids": sorted(val_pids)})

            if resume and os.path.exists(ckpt_f):
                ckpt    = torch.load(ckpt_f, map_location='cpu')
                if checkpoint_matches(ckpt, meta) and ckpt.get("training_complete") is True:
                    b_epoch = ckpt.get("epoch", max_epochs)
                    b_loss  = ckpt.get("best_loss", 0.0)
                    logger.info(f" -> Nap checkpoint hop le [{cfg_name}] Nep {f_idx}/5: Best Epoch = {b_epoch}")
                else:
                    logger.info(f" -> Bo qua checkpoint khong khop giao thuc: {ckpt_f}")
                    ckpt = None
            else:
                ckpt = None
            if ckpt is None:
                logger.info(f" -> Huan luyen [{cfg_name}] Nep {f_idx}/5 "
                            f"(Train: {len(tr_recs)} ngu canh, Val: {len(val_recs)} ngu canh)...")
                _, b_epoch, b_loss, _ = train_fold(
                    tr_recs, val_recs, lz=lz, beta=beta, hr_weight=hr_w, rr_weight=rr_w,
                    max_epochs=max_epochs, batch_size=batch_size, lr=lr,
                    patience=early_patience, lr_patience=lr_patience,
                    lr_factor=lr_factor, fs=fs, hr_band=hr_band, rr_band=rr_band,
                    weight_decay=weight_decay,
                    device=device, seed=seed + f_idx, save_checkpoint=ckpt_f,
                    checkpoint_metadata=meta, eps_f=cfg['training']['loss']['eps_f']
                )
                logger.info(f" -> Hoan tat [{cfg_name}] Nep {f_idx}/5: Best Epoch = {b_epoch}, Val Loss = {b_loss:.6f}")

            fold_best_epochs.append(b_epoch)
            fold_val_losses.append(b_loss)
            fold_details.append({
                "fold":                 f_idx,
                "train_patients_count": len(tr_pids),
                "val_patients_count":   len(val_pids),
                "best_epoch":           int(b_epoch),
                "best_val_loss":        float(b_loss)
            })

        median_ep = int(np.median(fold_best_epochs))
        logger.info(f"[{cfg_name}] Trung vi best epoch: {median_ep}, "
                    f"Val loss: {np.mean(fold_val_losses):.6f} +- {np.std(fold_val_losses):.6f}")

        cv_per_config[cfg_name] = {
            "folds":             fold_details,
            "median_best_epoch": median_ep,
            "val_loss_mean":     float(np.mean(fold_val_losses)),
            "val_loss_std":      float(np.std(fold_val_losses))
        }

    cv_path = output_path or os.path.join(ckpt_dir, "cv_results.json")
    if not os.path.isabs(cv_path):
        cv_path = os.path.join(BASE_DIR, cv_path)
    os.makedirs(os.path.dirname(cv_path), exist_ok=True)
    with open(cv_path, 'w', encoding='utf-8') as f:
        json.dump(cv_per_config, f, indent=2, ensure_ascii=False)
    logger.info(f"-> Da luu ket qua 5-Fold CV (6 cau hinh) tai: {cv_path}")

    return cv_per_config

def stage_train_final(cfg: dict, dev_records: list, cv_per_config: dict, device: str,
                      protocol_hash: str, logger: logging.Logger,
                      resume: bool = False) -> dict:
    logger.info("=== GIAI DOAN 3: HUAN LUYEN MO HINH CUOI TREN TOAN BO 36 DOI TUONG PHAT TRIEN (40 BAN GHI) ===")
    ckpt_dir   = os.path.join(BASE_DIR, "checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)

    batch_size = cfg["training"]["batch_contexts"]
    lr         = cfg["training"]["learning_rate"]
    seed       = cfg["project"]["seed"]
    default_epochs = cfg["training"]["max_epochs"]
    fs = cfg["data"]["sampling_rate"]
    hr_band = cfg["training"]["loss"]["hr_band"]
    rr_band = cfg["training"]["loss"]["rr_band"]
    weight_decay = cfg["training"]["weight_decay"]
    splits_path = os.path.join(BASE_DIR, cfg["data"]["splits_file"])
    data_path = os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy")

    checksums = {}
    for cfg_name, filename, lz, beta, hr_w, rr_w in ALL_CONFIGS:
        ckpt_path = os.path.join(ckpt_dir, filename)

        if cfg_name not in cv_per_config or len(cv_per_config[cfg_name].get("folds", [])) != 5:
            raise ValueError(f"Thieu ket qua nam nep cho cau hinh: {cfg_name}")
        target_epochs = int(np.median([f["best_epoch"] for f in cv_per_config[cfg_name]["folds"]]))
        if not 1 <= target_epochs <= default_epochs:
            raise ValueError(f"So epoch cuoi khong hop le: {cfg_name}: {target_epochs}")
        meta = checkpoint_metadata(cfg, protocol_hash, splits_path, data_path, cfg_name)

        if resume and os.path.exists(ckpt_path):
            ckpt = torch.load(ckpt_path, map_location='cpu')
            if (ckpt.get("trained_on_all_dev", False)
                    and checkpoint_matches(ckpt, meta, expected_epoch=target_epochs)):
                logger.info(f" -> Nap san mo hinh cuoi [{cfg_name}]: {ckpt_path}")
                checksums[cfg_name] = compute_file_sha256(ckpt_path)
                continue

        logger.info(f" -> Huan luyen cuoi [{cfg_name}] tren 36 doi tuong / 40 ban ghi, "
                    f"{len(dev_records)} ngu canh, {target_epochs} epochs...")
        train_final_full_dev(
            dev_records, lz=lz, beta=beta, hr_weight=hr_w, rr_weight=rr_w,
            target_epochs=target_epochs, batch_size=batch_size, lr=lr,
            fs=fs, hr_band=hr_band, rr_band=rr_band,
            weight_decay=weight_decay,
            device=device, seed=seed, save_checkpoint=ckpt_path,
            checkpoint_metadata=meta, eps_f=cfg['training']['loss']['eps_f']
        )
        checksums[cfg_name] = compute_file_sha256(ckpt_path)
        logger.info(f" -> Da luu [{cfg_name}] (SHA-256: {checksums[cfg_name][:12]}...) tai {ckpt_path}")

    return checksums

def stage_evaluate(cfg: dict, records: list, splits: dict, device: str,
                   logger: logging.Logger) -> dict:
    logger.info("=== GIAI DOAN 4: DANH GIA TREN 10 DOI TUONG KIEM THU (13 BAN GHI, 182 NGU CANH, 728 KHOI) ===")
    test_pids    = set(splits["test_pids"])
    test_records = [r for r in records if r["pid"] in test_pids]

    non_overlap_contexts = []
    for pid in sorted(test_pids):
        cur_p    = [r for r in test_records if r["pid"] == pid]
        last_end = -1.0
        for r in cur_p:
            if r["t_start"] >= last_end - 1e-4:
                non_overlap_contexts.append(r)
                last_end = r["t_end"]

    logger.info(f"Tong so ngu canh 32s khong chong lap: {len(non_overlap_contexts)} ({len(non_overlap_contexts) * 4} khoi 8s)")

    ckpt_dir = os.path.join(BASE_DIR, "checkpoints")
    protocol_hash = compute_protocol_hash(cfg)
    splits_path = os.path.join(BASE_DIR, cfg["data"]["splits_file"])
    data_path = os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy")
    models_dict = {}

    model_files = {
        "Autoencoder-MSE (8x)":        ("baseline_ae_mse_8x.pt",      115),
        "Autoencoder-MSE (16x)":       ("baseline_ae_mse_16x.pt",     52),
        "De xuat Day du (8x)":         ("best_model_8x.pt",           115),
        "De xuat Day du (16x)":        ("best_model_16x.pt",          52),
        "Boc tach B: Pho deu (8x)":   ("ablation_unweighted_8x.pt",   115),
        "Boc tach C: Uu tien HR (8x)": ("ablation_hr_only_8x.pt",     115),
    }

    for m_name, (fname, lz) in model_files.items():
        m_path = os.path.join(ckpt_dir, fname)
        assert os.path.exists(m_path), f"Thieu tep checkpoint: {m_path}"
        m = PPGAutoencoder(lz=lz)
        ckpt = torch.load(m_path, map_location='cpu', weights_only=False)
        expected = checkpoint_metadata(cfg, protocol_hash, splits_path, data_path, m_name)
        validate_saved_checkpoint(m_path, ckpt, expected)
        if int(ckpt.get("lz", -1)) != lz:
            raise RuntimeError(f"Sai kich thuoc latent trong checkpoint: {m_path}")
        m.load_state_dict(ckpt["model_state_dict"])
        m = m.to(device)
        m.eval()
        models_dict[m_name] = m
        logger.info(f" -> Da nap checkpoint: [{m_name}]")

    eval_results = evaluate_all_methods_on_test_set(
        non_overlapping_contexts=non_overlap_contexts,
        models_dict=models_dict,
        output_dir=BASE_DIR,
        device=device,
        config=cfg
    )

    return eval_results

def stage_export_and_benchmark(cfg: dict, records: list, splits: dict, logger: logging.Logger) -> dict:
    logger.info("=== GIAI DOAN 5: XUAT ENCODER VA BENCHMARK THAM CHIEU TREN CPU ===")
    ckpt_dir        = os.path.join(BASE_DIR, "checkpoints")
    best_8x_ckpt    = os.path.join(ckpt_dir, "best_model_8x.pt")
    traced_path_8x  = os.path.join(ckpt_dir, "ppg_encoder_8x_traced.pt")
    best_16x_ckpt   = os.path.join(ckpt_dir, "best_model_16x.pt")
    traced_path_16x = os.path.join(ckpt_dir, "ppg_encoder_16x_traced.pt")

    protocol_hash = compute_protocol_hash(cfg)
    splits_path = os.path.join(BASE_DIR, cfg["data"]["splits_file"])
    data_path = os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy")
    for name, path in (("De xuat Day du (8x)", best_8x_ckpt),
                       ("De xuat Day du (16x)", best_16x_ckpt)):
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        expected = checkpoint_metadata(cfg, protocol_hash, splits_path, data_path, name)
        validate_saved_checkpoint(path, ckpt, expected)

    export_encoder_model(best_8x_ckpt, traced_path_8x, lz=115)
    logger.info(f"-> Da xuat TorchScript encoder 8x: {traced_path_8x}")
    export_encoder_model(best_16x_ckpt, traced_path_16x, lz=52)
    logger.info(f"-> Da xuat TorchScript encoder 16x: {traced_path_16x}")

    test_pids   = set(splits["test_pids"])
    test_recs   = [r for r in records if r["pid"] in test_pids]
    from utils.deployment_reference import load_raw_benchmark_blocks
    real_blocks, raw_sources = load_raw_benchmark_blocks(BASE_DIR, cfg, test_pids, minimum=120)

    bench_results = run_edge_benchmark(
        encoder_path_8x=traced_path_8x,
        encoder_path_16x=traced_path_16x,
        real_blocks=real_blocks,
        n_warmup=20,
        n_runs=100,
        seed=cfg["project"]["seed"],
        config=cfg,
        raw_sources=raw_sources
    )

    bench_results["traced_encoder_sha256"]     = compute_file_sha256(traced_path_8x)
    bench_results["traced_encoder_16x_sha256"] = compute_file_sha256(traced_path_16x)

    _export_sample_packets(test_recs, ckpt_dir, logger)
    return bench_results


def _export_sample_packets(test_recs: list, ckpt_dir: str, logger: logging.Logger):
    from utils.packet_codec import pack_payload, quantize_latent

    sample_block = None
    for r in test_recs:
        for b in r["blocks"]:
            if b["signal_raw"] is not None and len(b["signal_raw"]) == 1000:
                sample_block = b
                break
        if sample_block is not None:
            break

    if sample_block is None:
        logger.warning("Khong tim thay khoi mau hop le, bo qua xuat goi mau.")
        return

    for cr_label, lz, config_id, fname_bin, fname_json in [
        ("8x",  115, 1, "sample_packet_8x.bin",  "sample_packet_8x_meta.json"),
        ("16x",  52, 2, "sample_packet_16x.bin", "sample_packet_16x_meta.json"),
    ]:
        ckpt_file = os.path.join(ckpt_dir, f"best_model_{cr_label}.pt")
        if not os.path.exists(ckpt_file):
            logger.warning(f"Khong tim thay checkpoint {ckpt_file}, bo qua xuat goi mau {cr_label}.")
            continue

        model = PPGAutoencoder(lz=lz)
        state = torch.load(ckpt_file, map_location='cpu')
        model.load_state_dict(state["model_state_dict"])
        model.eval()

        x_norm = sample_block["signal_norm"]
        mu     = float(sample_block["mu"])
        s      = float(sample_block["s"])

        with torch.no_grad():
            inp = torch.tensor(x_norm.reshape(1, 1, 1000), dtype=torch.float32)
            z, _ = model.encoder(inp)
            z_np = z.cpu().numpy().squeeze()
            q, a = quantize_latent(z_np)

        pkt = pack_payload(version=1, config_id=config_id, sequence=0,
                           mu=mu, s=s, a=float(a), payload_int16=q)

        bin_path  = os.path.join(BASE_DIR, fname_bin)
        meta_path = os.path.join(BASE_DIR, fname_json)

        with open(bin_path, 'wb') as f:
            f.write(pkt)

        pkt_sha = hashlib.sha256(pkt).hexdigest()
        meta = {
            "description":       f"Goi mau {cr_label} tao tu encoder tren khoi BIDMC that",
            "version":           1,
            "config_id":         config_id,
            "sequence":          0,
            "compression_ratio": cr_label,
            "lz":                lz,
            "mu":                mu,
            "s":                 s,
            "scale_a":           float(a),
            "packet_bytes":      len(pkt),
            "sha256":            pkt_sha
        }
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        logger.info(f"-> Da xuat goi mau {cr_label}: {bin_path} ({len(pkt)} byte, SHA-256: {pkt_sha[:12]}...)")


def stage_report(logger: logging.Logger):
    logger.info("=== GIAI DOAN 6: SINH HINH ANH BIEU DO VA BAO CAO HOAN THIEN WORD ===")
    from generate_report_assets import generate_assets
    generate_assets()

    from plot_waveform_samples import plot_samples
    plot_samples()

    import subprocess
    document_python = os.environ.get("C5_DOCUMENT_PYTHON", os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "..", "..", ".cache", "codex-runtimes",
        "codex-primary-runtime", "dependencies", "python", "python.exe"))
    # The desktop artifact runtime is optional outside Codex; explicitly set
    # C5_DOCUMENT_PYTHON to the verified document environment when generating.
    if not os.path.isfile(document_python):
        document_python = sys.executable
    subprocess.run([document_python, os.path.join(BASE_DIR, "generate_final_report_v2.py")], check=True, cwd=BASE_DIR)
    out_docx = os.path.join(BASE_DIR, "Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.docx")
    logger.info(f"-> DA TAO THANH CONG BAO CAO TIEU LUAN TAI: {out_docx}")

    try:
        from update_docx_fields import update_docx_and_export_pdf
        update_docx_and_export_pdf()
        logger.info("-> DA CAP NHAT TOC VA XUAT PDF HOAN THIEN")
    except Exception as e:
        logger.warning(f"Loi cap nhat docx va xuat pdf: {e}")

def main():
    parser = argparse.ArgumentParser(description="Pipeline nghien cuu de tai C5 PPG Compression")
    parser.add_argument("--config", type=str, default="configs/c5.yaml")
    parser.add_argument("--stage", type=str, default="all",
                        choices=["all", "prepare", "cross_validate", "train_final",
                                 "evaluate", "benchmark", "report"])
    parser.add_argument("--resume", action="store_true", help="Giu nguyen checkpoint neu da co san")
    parser.add_argument("--cv-config-indices", type=str, default=None,
                        help="Danh sach chi so cau hinh CV, vi du 0,1")
    parser.add_argument("--cv-output", type=str, default=None,
                        help="Tep JSON rieng cho tien trinh CV song song")
    parser.add_argument("--device", type=str, default=None,
                        help="Thiet bi tinh toan (cuda hoac cpu)")
    args = parser.parse_args()

    log_file = os.path.join(BASE_DIR, "execution.log")
    logger   = setup_logger(log_file)
    logger.info(f"Bat dau quy trinh: config={args.config}, stage={args.stage}, resume={args.resume}")

    with open(os.path.join(BASE_DIR, args.config), 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    protocol_hash = compute_protocol_hash(cfg)
    logger.info(f"Protocol hash (SHA-256): {protocol_hash[:32]}...")

    device = args.device if args.device else ('cuda' if torch.cuda.is_available() else 'cpu')
    logger.info(f"Thiet bi tinh toan: {device}")

    data, splits = stage_prepare(cfg, logger)
    dev_records  = [r for r in data if r["pid"] in set(splits["dev_pids"])]

    cv_per_config = {}
    if args.stage in ["all", "cross_validate"]:
        selected_configs = None
        if args.cv_config_indices:
            indices = [int(x.strip()) for x in args.cv_config_indices.split(",") if x.strip()]
            if any(i < 0 or i >= len(ALL_CONFIGS) for i in indices):
                raise ValueError("Chi so cau hinh CV nam ngoai pham vi")
            selected_configs = [ALL_CONFIGS[i] for i in indices]
        cv_per_config = stage_cross_validate(
            cfg, dev_records, splits, device, protocol_hash, logger, resume=args.resume,
            selected_configs=selected_configs, output_path=args.cv_output
        )
        if args.cv_output:
            logger.info("Tien trinh CV rieng da hoan tat; khong ghi de ket qua tong hop.")
            return
    else:
        cv_path = os.path.join(BASE_DIR, "checkpoints", "cv_results.json")
        if os.path.exists(cv_path):
            try:
                with open(cv_path, 'r', encoding='utf-8') as f:
                    cv_per_config = json.load(f)
            except Exception:
                cv_per_config = {}

    checksums = {}
    if args.stage in ["all", "train_final"]:
        checksums = stage_train_final(
            cfg, dev_records, cv_per_config, device, protocol_hash, logger, resume=args.resume
        )

    eval_results = {}
    if args.stage in ["all", "evaluate"]:
        eval_results = stage_evaluate(cfg, data, splits, device, logger)

    bench_results = {}
    if args.stage in ["all", "benchmark"]:
        bench_results = stage_export_and_benchmark(cfg, data, splits, logger)

    summary_path     = os.path.join(BASE_DIR, "results_summary.json")
    combined_summary = {}
    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            try:
                combined_summary = json.load(f)
            except Exception:
                combined_summary = {}

    if combined_summary.get("protocol_hash") != protocol_hash:
        logger.info("Giao thuc da thay doi; khong giu ket qua cu trong ban tong hop moi.")
        combined_summary = {}

    combined_summary["schema_version"] = "2.1"
    combined_summary['training_provenance'] = {
        'origin_verified': False,
        'status': 'supplied_checkpoint_replay_after_recorded_hash_overwrite',
        'evidence': 'checkpoints/existing_run_evidence.json',
        'disclosure': 'Checkpoint bytes are preserved; their original training-source hashes are not recoverable from overwritten metadata. Evaluation replay does not verify original training provenance.'
    }
    combined_summary["protocol_hash"]  = protocol_hash
    combined_summary["config_version"] = cfg["project"]["version"]
    combined_summary["generated_at"]   = time.strftime("%Y-%m-%d %H:%M:%S")

    if cv_per_config:
        if "folds" in cv_per_config:
            combined_summary["cross_validation"] = cv_per_config
            combined_summary["cross_validation_per_config"] = {"De xuat Day du (8x)": cv_per_config}
        else:
            combined_summary["cross_validation_per_config"] = cv_per_config
            first_cfg = next(iter(cv_per_config.values()), {})
            combined_summary["cross_validation"] = first_cfg

    if eval_results:
        combined_summary["evaluation"] = eval_results
        combined_summary.setdefault('stage_fingerprints', {})['evaluation'] = stage_fingerprint(BASE_DIR, cfg, [os.path.join(BASE_DIR, 'checkpoints', c[1]) for c in ALL_CONFIGS])
        combined_summary.setdefault("stage_protocol_hashes", {})["evaluation"] = protocol_hash
    if bench_results:
        combined_summary["benchmark"] = bench_results
        combined_summary.setdefault('stage_fingerprints', {})['benchmark'] = stage_fingerprint(BASE_DIR, cfg, [os.path.join(BASE_DIR, 'checkpoints', c[1]) for c in ALL_CONFIGS])
        combined_summary.setdefault("stage_protocol_hashes", {})["benchmark"] = protocol_hash
    if checksums:
        combined_summary.setdefault("checkpoint_sha256", {}).update(checksums)
        combined_summary.setdefault("stage_protocol_hashes", {})["train_final"] = protocol_hash

    valid_keys = ["schema_version", "protocol_hash", "config_version", "generated_at",
                  "cross_validation", "cross_validation_per_config",
                  "evaluation", "benchmark", "checkpoint_sha256", "stage_protocol_hashes", "stage_fingerprints", "training_provenance"]
    combined_summary = {k: v for k, v in combined_summary.items() if k in valid_keys}

    def json_default(obj):
        if isinstance(obj, bytes):
            return obj.hex()
        if isinstance(obj, (np.floating, np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.integer, np.int32, np.int64)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return str(obj)

    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(strict_json(combined_summary), f, indent=2, ensure_ascii=False, default=json_default, allow_nan=False)
    logger.info(f"-> Da cap nhat results_summary.json (protocol_hash: {protocol_hash[:16]}...)")

    if args.stage in ["all", "report"]:
        stage_report(logger)

    manifest_path = os.path.join(BASE_DIR, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r', encoding='utf-8') as f:
            try:
                manifest = json.load(f)
            except Exception:
                manifest = {}

    ckpt_dir  = os.path.join(BASE_DIR, "checkpoints")
    all_ckpts = {}
    if os.path.exists(ckpt_dir):
        for f_name in sorted(os.listdir(ckpt_dir)):
            if f_name.endswith('.pt'):
                all_ckpts[f_name] = compute_file_sha256(os.path.join(ckpt_dir, f_name))

    files_to_hash = {
        "config_yaml":             os.path.join(BASE_DIR, args.config),
        "splits_subject_json":     os.path.join(BASE_DIR, cfg["data"]["splits_file"]),
        "processed_dataset_npy":   os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy"),
        "preprocessing_audit_json": os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "preprocessing_audit.json"),
        "dev_uncompressed_baseline_json": os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "dev_uncompressed_baseline.json"),
        "results_summary_json":    summary_path,
        "results_per_window_csv":  os.path.join(BASE_DIR, "results_per_window.csv"),
        "results_per_context_csv": os.path.join(BASE_DIR, "results_per_context.csv"),
        "results_per_patient_csv": os.path.join(BASE_DIR, "results_per_patient.csv"),
        "cv_results_json":         os.path.join(ckpt_dir, "cv_results.json"),
        "traced_encoder_8x":       os.path.join(ckpt_dir, "ppg_encoder_8x_traced.pt"),
        "traced_encoder_16x":      os.path.join(ckpt_dir, "ppg_encoder_16x_traced.pt"),
        "sample_packet_8x_bin":    os.path.join(BASE_DIR, "sample_packet_8x.bin"),
        "sample_packet_16x_bin":   os.path.join(BASE_DIR, "sample_packet_16x.bin"),
        "sample_packet_8x_meta":   os.path.join(BASE_DIR, "sample_packet_8x_meta.json"),
        "sample_packet_16x_meta":  os.path.join(BASE_DIR, "sample_packet_16x_meta.json"),
        "final_report_docx":       os.path.join(BASE_DIR, "Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.docx"),
        "final_report_pdf":        os.path.join(BASE_DIR, "Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.pdf"),
        "requirements_txt":        os.path.join(BASE_DIR, "requirements.txt"),
    }
    file_hashes = {}
    for k, p in files_to_hash.items():
        if os.path.exists(p):
            file_hashes[k] = compute_file_sha256(p)

    raw_dir = os.path.join(BASE_DIR, cfg["data"]["raw_dir"])
    raw_data_checksums = {}
    if os.path.isdir(raw_dir):
        for raw_file in sorted(os.listdir(raw_dir)):
            raw_path = os.path.join(raw_dir, raw_file)
            if os.path.isfile(raw_path):
                raw_data_checksums[raw_file] = compute_file_sha256(raw_path)

    source_files = [
        "prepare_data.py", "train.py", "evaluate.py", "benchmark_edge.py",
        "export_encoder.py", "generate_report_assets.py", "generate_final_report_v2.py",
        "models/autoencoder.py", "models/dct_baseline.py", "utils/estimators.py",
        "utils/packet_codec.py", "utils/spectral_loss.py", "tests/test_pipeline.py",
        "utils/deployment_reference.py", "tests/test_deployment_reference.py",
        "evaluate_dev_uncompressed.py", "run_experiments.py", "update_docx_fields.py",
        "plot_waveform_samples.py", "README.md", "inspect_rr_features.py", "analyze_rr.py",
        "demo_pipeline.py", "verify_delivery.py", "tests/test_delivery.py"
    ]
    source_checksums = {
        rel: compute_file_sha256(os.path.join(BASE_DIR, rel))
        for rel in source_files if os.path.exists(os.path.join(BASE_DIR, rel))
    }

    manifest.update({
        "project":               cfg["project"]["name"],
        "version":               cfg["project"]["version"],
        "protocol_hash":         protocol_hash,
        "schema_version":        "2.0",
        "timestamp":             time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed":                  cfg["project"]["seed"],
        "config":                cfg,
        "data_records_count":    len(data),
        "test_subjects":         splits.get("test_subjects", splits.get("test_pids")),
        "dev_subjects":          splits.get("dev_subjects", splits.get("dev_pids")),
        "test_records":          splits.get("test_records", splits.get("test_pids")),
        "dev_records":           splits.get("dev_records", splits.get("dev_pids")),
        "checkpoint_checksums":  all_ckpts,
        "file_checksums_sha256": file_hashes,
        "raw_data_checksums_sha256": raw_data_checksums,
        "source_code_checksums_sha256": source_checksums
    })
    if bench_results and "environment" in bench_results:
        manifest["system_environment"] = bench_results["environment"]
    elif "system_environment" not in manifest:
        from benchmark_edge import get_system_environment_info
        manifest["system_environment"] = get_system_environment_info()

    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    logger.info(f"-> Da cap nhat manifest.json (protocol_hash: {protocol_hash[:16]}...)")


    logger.info("=== QUY TRINH DA HOAN THAT ===")
    logger.info(f"  - Cau hinh: {args.config} (v{cfg['project']['version']})")
    logger.info(f"  - Giai doan: {args.stage}")
    logger.info(f"  - Protocol hash: {protocol_hash[:32]}...")


if __name__ == "__main__":
    main()
