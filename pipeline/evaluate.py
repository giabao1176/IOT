import os
import csv
import json
import numpy as np
import pandas as pd
import torch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from utils.estimators import estimate_hr_peaks, estimate_rr_riiv_riav, rr_options
from utils.packet_codec import (
    pack_payload, unpack_payload, quantize_latent, dequantize_latent, PacketSequenceTracker
)
from models.autoencoder import PPGAutoencoder
from models.dct_baseline import DCTBaseline

def calc_prd_prdc_snrc(x_raw: np.ndarray, x_rec: np.ndarray) -> tuple[float, float, float]:
    diff = x_raw - x_rec
    diff_sq = np.sum(diff ** 2)
    raw_sq = np.sum(x_raw ** 2)
    
    if raw_sq < 1e-9:
        prd = np.nan
    else:
        prd = float(np.sqrt(diff_sq / raw_sq) * 100.0)
        
    x_centered = x_raw - np.mean(x_raw)
    centered_sq = np.sum(x_centered ** 2)
    if centered_sq < 1e-9:
        prdc = np.nan
        snrc = np.nan
    else:
        prdc = float(np.sqrt(diff_sq / centered_sq) * 100.0)
        if diff_sq == 0.0:
            snrc = float('inf')
        else:
            snrc = float(10.0 * np.log10(centered_sq / diff_sq))
            
    return prd, prdc, snrc

def bootstrap_subject_ci(per_patient_vals: dict, n_boot: int = 2000, ci: float = 0.95, seed: int = 2026) -> tuple[float, float, float]:
    vals = [v for v in per_patient_vals.values() if not np.isnan(v)]
    if len(vals) == 0:
        return np.nan, np.nan, np.nan
    vals_arr = np.array(vals, dtype=np.float64)
    mean_val = float(np.mean(vals_arr))
    if np.isposinf(vals_arr).all():
        return mean_val, np.nan, np.nan
    
    rng = np.random.RandomState(seed)
    n = len(vals_arr)
    boot_means = [np.mean(rng.choice(vals_arr, size=n, replace=True)) for _ in range(n_boot)]
    
    alpha = (1.0 - ci) / 2.0
    low = float(np.percentile(boot_means, alpha * 100.0))
    high = float(np.percentile(boot_means, (1.0 - alpha) * 100.0))
    return mean_val, low, high

def bootstrap_paired_diff_ci(diff_per_patient: dict, n_boot: int = 2000, ci: float = 0.95, seed: int = 2026) -> tuple[float, float, float]:
    vals = [v for v in diff_per_patient.values() if not np.isnan(v)]
    if len(vals) == 0:
        return np.nan, np.nan, np.nan
    vals_arr = np.array(vals, dtype=np.float64)
    mean_val = float(np.mean(vals_arr))
    
    rng = np.random.RandomState(seed)
    n = len(vals_arr)
    boot_means = [np.mean(rng.choice(vals_arr, size=n, replace=True)) for _ in range(n_boot)]
    
    alpha = (1.0 - ci) / 2.0
    low = float(np.percentile(boot_means, alpha * 100.0))
    high = float(np.percentile(boot_means, (1.0 - alpha) * 100.0))
    return mean_val, low, high

def reconstruct_block_with_model(model, x_norm, mu, s, lz, config_id, seq, device):
    model.eval()
    with torch.no_grad():
        inp = torch.tensor(x_norm.reshape(1, 1, 1000), dtype=torch.float32).to(device)
        z, scale_a = model.encoder(inp)
        z_np = z.cpu().numpy().squeeze()
        q, a = quantize_latent(z_np)
        
        pkt = pack_payload(version=1, config_id=config_id, sequence=seq, mu=mu, s=s, a=a, payload_int16=q)
        unp = unpack_payload(pkt, expected_lz=lz, expected_config_id=config_id)
        assert unp["valid"] and unp["crc_valid"], f"Gói tin không hợp lệ: {unp['error_msg']}"
        
        z_rec = dequantize_latent(unp["payload_int16"], unp["a"])
        z_rec_tensor = torch.tensor(z_rec.reshape(1, -1), dtype=torch.float32).to(device)
        x_hat_norm = model.decoder(z_rec_tensor).cpu().numpy().squeeze()
        x_rec = x_hat_norm * unp["s"] + unp["mu"]
        return x_rec, unp

def evaluate_all_methods_on_test_set(non_overlapping_contexts: list,
                                     models_dict: dict,
                                     output_dir: str,
                                     device: str = 'cpu',
                                     config: dict = None) -> dict:
    if not non_overlapping_contexts:
        raise ValueError('Không có ngữ cảnh kiểm thử')
    from functools import partial
    boot_options = dict(n_boot=int(config['evaluation']['bootstrap_reps']),
                        ci=float(config['evaluation']['bootstrap_ci']), seed=int(config['project']['seed']))
    bootstrap_subject_ci = partial(globals()['bootstrap_subject_ci'], **boot_options)
    bootstrap_paired_diff_ci = partial(globals()['bootstrap_paired_diff_ci'], **boot_options)
    fs = int(config["data"]["sampling_rate"])
    hr_cfg = config.get("evaluation", {}).get("hr_estimator", {})
    hr_min_peaks = int(hr_cfg.get("min_peaks", 3))
    hr_min_bpm = float(hr_cfg.get("min_bpm", 30.0))
    hr_max_bpm = float(hr_cfg.get("max_bpm", 210.0))
    hr_prominence_factor = float(hr_cfg.get("prominence_factor", 0.3))
    methods = [
        "Uncompressed",
        "DCT-1D (8x)",
        "DCT-1D (16x)",
        "Autoencoder-MSE (8x)",
        "Autoencoder-MSE (16x)",
        "De xuat Day du (8x)",
        "De xuat Day du (16x)",
        "Boc tach B: Pho deu (8x)",
        "Boc tach C: Uu tien HR (8x)"
    ]
    
    dct_8x = DCTBaseline(lz=115)
    dct_16x = DCTBaseline(lz=52)
    
    # Chuẩn bị cấu trúc lưu kết quả theo từng cửa sổ và ngữ cảnh
    window_rows = []
    
    # Từ điển theo dõi per-window cho từng method
    # block_key: (ctx_idx, b_idx)
    raw_blocks_store = {}
    rec_blocks_store = {m: {} for m in methods}
    hr_ref_store = {}
    hr_est_store = {m: {} for m in methods}
    hr_err_store = {m: {} for m in methods}
    prd_store = {m: {} for m in methods}
    prdc_store = {m: {} for m in methods}
    snrc_store = {m: {} for m in methods}
    
    rr_ref_store = {}
    rr_est_store = {m: {} for m in methods}
    rr_err_store = {m: {} for m in methods}
    rr_status_store = {m: {} for m in methods}
    boundary_amp_store = {m: [] for m in methods}
    boundary_slope_store = {m: [] for m in methods}
    
    # Lưu metadata của context và block
    block_meta = []
    ctx_meta = []
    
    global_block_idx = 0
    seq_counter = 0
    
    for c_idx, ctx in enumerate(non_overlapping_contexts):
        pid = ctx["pid"]
        t_start_ctx = ctx["t_start"]
        t_end_ctx = ctx["t_end"]
        rr_ref = ctx["rr_ref"]
        
        ctx_meta.append({
            "ctx_idx": c_idx,
            "pid": pid,
            "t_start": t_start_ctx,
            "t_end": t_end_ctx,
            "rr_ref": rr_ref
        })
        rr_ref_store[c_idx] = rr_ref
        
        blocks = ctx["blocks"]
        ctx_blocks_rec = {m: [] for m in methods}
        
        for b_idx, b in enumerate(blocks):
            x_raw = b["signal_raw"]
            x_norm = b["signal_norm"]
            mu = b["mu"]
            s = b["s"]
            hr_ref = b["hr_ref"]
            
            b_key = (c_idx, b_idx)
            raw_blocks_store[b_key] = x_raw
            hr_ref_store[b_key] = hr_ref
            
            block_meta.append({
                "global_idx": global_block_idx,
                "ctx_idx": c_idx,
                "b_idx": b_idx,
                "pid": pid,
                "t_start": b["t_start"],
                "t_end": b["t_end"],
                "hr_ref": hr_ref
            })
            
            for m in methods:
                if m == "Uncompressed":
                    x_rec = x_raw.copy()
                elif m == "DCT-1D (8x)":
                    pkt = dct_8x.encode(x_norm, version=1, config_id=1, seq=seq_counter, mu=mu, s=s)
                    x_rec_t, _ = dct_8x.decode(pkt, target_len=1000, expected_config_id=1)
                    if x_rec_t is None:
                        raise RuntimeError(f"Giải mã DCT-1D (8x) thất bại tại context {c_idx}, block {b_idx}; không tự ý gán 0")
                    x_rec = x_rec_t.squeeze()
                elif m == "DCT-1D (16x)":
                    pkt = dct_16x.encode(x_norm, version=1, config_id=2, seq=seq_counter, mu=mu, s=s)
                    x_rec_t, _ = dct_16x.decode(pkt, target_len=1000, expected_config_id=2)
                    if x_rec_t is None:
                        raise RuntimeError(f"Giải mã DCT-1D (16x) thất bại tại context {c_idx}, block {b_idx}; không tự ý gán 0")
                    x_rec = x_rec_t.squeeze()
                elif m == "Autoencoder-MSE (8x)":
                    model = models_dict["Autoencoder-MSE (8x)"]
                    x_rec, _ = reconstruct_block_with_model(model, x_norm, mu, s, lz=115, config_id=1, seq=seq_counter, device=device)
                elif m == "Autoencoder-MSE (16x)":
                    model = models_dict["Autoencoder-MSE (16x)"]
                    x_rec, _ = reconstruct_block_with_model(model, x_norm, mu, s, lz=52, config_id=2, seq=seq_counter, device=device)
                elif m == "De xuat Day du (8x)":
                    model = models_dict["De xuat Day du (8x)"]
                    x_rec, _ = reconstruct_block_with_model(model, x_norm, mu, s, lz=115, config_id=1, seq=seq_counter, device=device)
                elif m == "De xuat Day du (16x)":
                    model = models_dict["De xuat Day du (16x)"]
                    x_rec, _ = reconstruct_block_with_model(model, x_norm, mu, s, lz=52, config_id=2, seq=seq_counter, device=device)
                elif m == "Boc tach B: Pho deu (8x)":
                    model = models_dict["Boc tach B: Pho deu (8x)"]
                    x_rec, _ = reconstruct_block_with_model(model, x_norm, mu, s, lz=115, config_id=1, seq=seq_counter, device=device)
                elif m == "Boc tach C: Uu tien HR (8x)":
                    model = models_dict["Boc tach C: Uu tien HR (8x)"]
                    x_rec, _ = reconstruct_block_with_model(model, x_norm, mu, s, lz=115, config_id=1, seq=seq_counter, device=device)
                else:
                    x_rec = x_raw.copy()
                    
                rec_blocks_store[m][b_key] = x_rec
                ctx_blocks_rec[m].append(x_rec)
                
                # PRD & SNR
                prd, prdc, snrc = calc_prd_prdc_snrc(x_raw, x_rec)
                prd_store[m][b_key] = prd
                prdc_store[m][b_key] = prdc
                snrc_store[m][b_key] = snrc
                
                # HR estimation
                hr_est = estimate_hr_peaks(
                    x_rec, fs=fs, min_peaks=hr_min_peaks, min_bpm=hr_min_bpm,
                    max_bpm=hr_max_bpm, prominence_factor=hr_prominence_factor
                )
                hr_est_store[m][b_key] = hr_est
                if not np.isnan(hr_ref) and not np.isnan(hr_est):
                    hr_err_store[m][b_key] = abs(hr_est - hr_ref)
                else:
                    hr_err_store[m][b_key] = np.nan
                    
            global_block_idx += 1
            seq_counter += 1
            
        if not config or "search_band" not in config.get("evaluation", {}).get("rr_estimator", {}):
            raise ValueError("Thieu evaluation.rr_estimator.search_band trong cau hinh")
        rr_cfg = config["evaluation"]["rr_estimator"]
        search_band = tuple(rr_cfg["search_band"])
        max_discrepancy = float(rr_cfg.get("max_discrepancy", 3.0))
        target_fs = float(rr_cfg.get("sampling_rate", 4.0))
        min_coverage_sec = float(rr_cfg.get("min_coverage_sec", 24.0))
        
        raw_stitched_32s = np.concatenate([raw_blocks_store[(c_idx, b)] for b in range(4)])
        junction_indices = [1000, 2000, 3000]

        # Ước lượng nhịp thở RR cho từng phương pháp trên ngữ cảnh 32s ghép nối
        for m in methods:
            stitched_32s = np.concatenate(ctx_blocks_rec[m])
            rr_est, st = estimate_rr_riiv_riav(
                stitched_32s, fs=fs,
                **rr_options(config)
            )
            rr_est_store[m][c_idx] = rr_est
            rr_status_store[m][c_idx] = st
            if not np.isnan(rr_ref) and not np.isnan(rr_est):
                rr_err_store[m][c_idx] = abs(rr_est - rr_ref)
            else:
                rr_err_store[m][c_idx] = np.nan

            # Phân tích sai lệch biên độ và độ dốc tại 3 điểm nối khối
            for j_idx in junction_indices:
                raw_amp_step = raw_stitched_32s[j_idx] - raw_stitched_32s[j_idx - 1]
                rec_amp_step = stitched_32s[j_idx] - stitched_32s[j_idx - 1]
                boundary_amp_store[m].append(float(abs(rec_amp_step - raw_amp_step)))

                raw_slope = (raw_stitched_32s[j_idx + 1] - raw_stitched_32s[j_idx]) - (raw_stitched_32s[j_idx - 1] - raw_stitched_32s[j_idx - 2])
                rec_slope = (stitched_32s[j_idx + 1] - stitched_32s[j_idx]) - (stitched_32s[j_idx - 1] - stitched_32s[j_idx - 2])
                boundary_slope_store[m].append(float(abs(rec_slope - raw_slope)))

    # -------------------------------------------------------------------------
    # XÁC ĐỊNH TẬP GIAO HỢP LỆ (INTERSECTION SETS)
    # -------------------------------------------------------------------------
    all_b_keys = list(hr_ref_store.keys())
    common_valid_hr_keys = []
    for b_key in all_b_keys:
        if not np.isnan(hr_ref_store[b_key]):
            all_valid = True
            for m in methods:
                if np.isnan(hr_err_store[m][b_key]):
                    all_valid = False
                    break
            if all_valid:
                common_valid_hr_keys.append(b_key)
                
    all_ctx_keys = list(rr_ref_store.keys())
    common_valid_rr_keys = []
    for c_idx in all_ctx_keys:
        if not np.isnan(rr_ref_store[c_idx]):
            all_valid = True
            for m in methods:
                if np.isnan(rr_err_store[m][c_idx]):
                    all_valid = False
                    break
            if all_valid:
                common_valid_rr_keys.append(c_idx)
                
    print(f"\n-> Tap giao hop le HR: {len(common_valid_hr_keys)} / {len(all_b_keys)} khoi 8s")
    print(f"-> Tap giao hop le RR: {len(common_valid_rr_keys)} / {len(all_ctx_keys)} ngu canh 32s")
    
    # -------------------------------------------------------------------------
    # XUẤT RESULTS_PER_WINDOW.CSV
    # -------------------------------------------------------------------------
    csv_window_path = os.path.join(output_dir, "results_per_window.csv")
    window_headers = [
        "block_global_idx", "context_idx", "block_idx", "pid", "t_start", "t_end",
        "hr_ref", "in_common_hr_intersection"
    ]
    for m in methods:
        clean_m = m.replace(" ", "_").replace(":", "").replace("(", "").replace(")", "").replace("-", "_")
        window_headers.extend([
            f"prd_{clean_m}", f"prdc_{clean_m}", f"snrc_{clean_m}", f"hr_est_{clean_m}", f"hr_err_{clean_m}", f"dr_hr_{clean_m}"
        ])
        
    with open(csv_window_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(window_headers)
        for bm in block_meta:
            b_key = (bm["ctx_idx"], bm["b_idx"])
            in_common = (b_key in common_valid_hr_keys)
            row = [
                bm["global_idx"], bm["ctx_idx"], bm["b_idx"], bm["pid"],
                f"{bm['t_start']:.2f}", f"{bm['t_end']:.2f}",
                f"{bm['hr_ref']:.2f}" if not np.isnan(bm["hr_ref"]) else "",
                1 if in_common else 0
            ]
            for m in methods:
                prd = prd_store[m][b_key]
                prdc = prdc_store[m][b_key]
                snrc = snrc_store[m][b_key]
                hr_est = hr_est_store[m][b_key]
                hr_err = hr_err_store[m][b_key]
                u_est = hr_est_store["Uncompressed"][b_key]
                dr_hr = abs(hr_est - u_est) if (not np.isnan(hr_est) and not np.isnan(u_est) and in_common) else np.nan
                row.extend([
                    f"{prd:.4f}" if not np.isnan(prd) else "",
                    f"{prdc:.4f}" if not np.isnan(prdc) else "",
                    f"{snrc:.4f}" if not np.isnan(snrc) else "",
                    f"{hr_est:.2f}" if not np.isnan(hr_est) else "",
                    f"{hr_err:.2f}" if not np.isnan(hr_err) else "",
                    f"{dr_hr:.2f}" if not np.isnan(dr_hr) else ""
                ])
            writer.writerow(row)
            
    print(f"-> Da xuat ket qua chi tiet tung cua so: {csv_window_path}")

    # -------------------------------------------------------------------------
    # XUẤT RESULTS_PER_CONTEXT.CSV CHO CHỈ SỐ NHỊP THỞ (140 NGỮ CẢNH 32 GIÂY)
    # -------------------------------------------------------------------------
    csv_context_path = os.path.join(output_dir, "results_per_context.csv")
    ctx_pid_map = {cm["ctx_idx"]: cm["pid"] for cm in ctx_meta}
    context_headers = ["ctx_idx", "pid", "rr_ref", "in_rr_intersection"]
    for m in methods:
        m_slug = m.replace(" ", "_").replace(":", "").replace("(", "").replace(")", "").replace("-", "_")
        context_headers.extend([
            f"{m_slug}_rr_est", f"{m_slug}_rr_err", f"{m_slug}_valid",
            f"{m_slug}_coverage_sec", f"{m_slug}_grid_points",
            f"{m_slug}_welch_nperseg", f"{m_slug}_welch_nfft",
            f"{m_slug}_riiv_riav_discrepancy", f"{m_slug}_feature_max_gap_sec",
            f"{m_slug}_supported_high_hz", f"{m_slug}_failure_reason", f"{m_slug}_dr_rr"
        ])
        
    with open(csv_context_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(context_headers)
        for c_idx in sorted(all_ctx_keys):
            pid = ctx_pid_map[c_idx]
            ref = rr_ref_store[c_idx]
            in_inter = 1 if c_idx in common_valid_rr_keys else 0
            row = [c_idx, pid, f"{ref:.2f}" if not np.isnan(ref) else "", in_inter]
            for m in methods:
                est = rr_est_store[m][c_idx]
                err = rr_err_store[m][c_idx]
                status = rr_status_store[m][c_idx]
                valid = 0 if np.isnan(err) else 1
                u_rr_est = rr_est_store["Uncompressed"][c_idx]
                dr_rr = abs(est - u_rr_est) if (not np.isnan(est) and not np.isnan(u_rr_est) and in_inter) else np.nan
                row.extend([
                    f"{est:.2f}" if not np.isnan(est) else "",
                    f"{err:.2f}" if not np.isnan(err) else "",
                    valid,
                    f"{status['coverage_sec']:.3f}",
                    status["grid_points"],
                    status["welch_nperseg"],
                    status["welch_nfft"],
                    f"{status['discrepancy']:.3f}" if not np.isnan(status["discrepancy"]) else "",
                    status["feature_max_gap_sec"], status["supported_high_hz"], status["failure_reason"],
                    f"{dr_rr:.2f}" if not np.isnan(dr_rr) else ""
                ])
            writer.writerow(row)
    print(f"-> Da xuat ket qua chi tiet tung ngu canh 32s (RR): {csv_context_path}")

    # -------------------------------------------------------------------------
    # XUẤT RESULTS_PER_PATIENT.CSV VÀ TÍNH METRICS THEO ĐỐI TƯỢNG NGUỒN MIMIC
    # -------------------------------------------------------------------------
    splits_path = os.path.join(BASE_DIR, "data", "splits_subject.json")
    record_to_subject = {}
    subject_to_records = {}
    if os.path.exists(splits_path):
        with open(splits_path, "r", encoding="utf-8") as sf:
            sp_data = json.load(sf)
            record_to_subject = {int(k): v for k, v in sp_data.get("record_to_subject", {}).items()}
            subject_to_records = sp_data.get("subject_to_records", {})

    for bm in block_meta:
        bm["subject_id"] = record_to_subject.get(bm["pid"], f"s_{bm['pid']:02d}")
    for cm in ctx_meta:
        cm["subject_id"] = record_to_subject.get(cm["pid"], f"s_{cm['pid']:02d}")

    subject_ids = sorted(list(set(bm["subject_id"] for bm in block_meta)))
    test_record_ids = sorted(list(set(bm["pid"] for bm in block_meta)))
    sub_primary_pid = {s: (subject_to_records.get(s, [1])[0] if s in subject_to_records else 1) for s in subject_ids}
    sub_recs_str = {s: ",".join(str(r) for r in subject_to_records.get(s, [sub_primary_pid[s]])) for s in subject_ids}

    csv_patient_path = os.path.join(output_dir, "results_per_patient.csv")
    patient_headers = [
        "subject_id", "record_ids", "pid", "method", "n_blocks", "prd_mean", "prd_std", "prdc_mean", "prdc_std", "snrc_mean", "snrc_std",
        "n_hr_eval_intersection", "mae_hr_intersection", "n_hr_eval_indiv", "mae_hr_indiv", "hr_coverage_pct", "dr_hr",
        "n_rr_eval_intersection", "mae_rr_intersection", "n_rr_eval_indiv", "mae_rr_indiv", "rr_coverage_pct", "dr_rr"
    ]
    
    results_summary = {
        "metadata": {
            "test_subjects": subject_ids,
            "test_records": test_record_ids,
            "subject_to_records": subject_to_records,
            "total_contexts": len(non_overlapping_contexts),
            "total_blocks": len(block_meta),
            "hr_intersection_size": len(common_valid_hr_keys),
            "rr_intersection_size": len(common_valid_rr_keys)
        },
        "methods": {}
    }
    
    with open(csv_patient_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(patient_headers)
        
        for m in methods:
            # Per patient aggregations for this method
            p_prd_means = {}
            p_prdc_means = {}
            p_snrc_means = {}
            p_hr_errs_inter = {s: [] for s in subject_ids}
            p_hr_errs_indiv = {s: [] for s in subject_ids}
            p_rr_errs_inter = {s: [] for s in subject_ids}
            p_rr_errs_indiv = {s: [] for s in subject_ids}
            p_hr_dr = {s: [] for s in subject_ids}
            p_rr_dr = {s: [] for s in subject_ids}
            
            p_tot_hr = {s: 0 for s in subject_ids}
            p_tot_rr = {s: 0 for s in subject_ids}
            
            for bm in block_meta:
                s = bm["subject_id"]
                b_key = (bm["ctx_idx"], bm["b_idx"])
                if not np.isnan(bm["hr_ref"]):
                    p_tot_hr[s] += 1
                    err_i = hr_err_store[m][b_key]
                    if not np.isnan(err_i):
                        p_hr_errs_indiv[s].append(err_i)
                    if b_key in common_valid_hr_keys:
                        p_hr_errs_inter[s].append(err_i)
                        est_m = hr_est_store[m][b_key]
                        est_u = hr_est_store["Uncompressed"][b_key]
                        if not np.isnan(est_m) and not np.isnan(est_u):
                            p_hr_dr[s].append(abs(est_m - est_u))
                        
            for cm in ctx_meta:
                s = cm["subject_id"]
                c_idx = cm["ctx_idx"]
                if not np.isnan(cm["rr_ref"]):
                    p_tot_rr[s] += 1
                    err_r = rr_err_store[m][c_idx]
                    if not np.isnan(err_r):
                        p_rr_errs_indiv[s].append(err_r)
                    if c_idx in common_valid_rr_keys:
                        p_rr_errs_inter[s].append(err_r)
                        est_m = rr_est_store[m][c_idx]
                        est_u = rr_est_store["Uncompressed"][c_idx]
                        if not np.isnan(est_m) and not np.isnan(est_u):
                            p_rr_dr[s].append(abs(est_m - est_u))
                        
            for s in subject_ids:
                s_blocks = [bm for bm in block_meta if bm["subject_id"] == s]
                s_prd_vals = [prd_store[m][(bm["ctx_idx"], bm["b_idx"])] for bm in s_blocks]
                s_prdc_vals = [prdc_store[m][(bm["ctx_idx"], bm["b_idx"])] for bm in s_blocks]
                s_snrc_vals = [snrc_store[m][(bm["ctx_idx"], bm["b_idx"])] for bm in s_blocks]
                
                prd_m = float(np.nanmean(s_prd_vals)) if len(s_prd_vals) > 0 else np.nan
                prd_s = float(np.nanstd(s_prd_vals)) if len(s_prd_vals) > 0 else np.nan
                prdc_m = float(np.mean(s_prdc_vals)) if len(s_prdc_vals) > 0 else np.nan
                prdc_s = float(np.std(s_prdc_vals)) if len(s_prdc_vals) > 0 else np.nan
                snrc_m = float(np.mean(s_snrc_vals)) if len(s_snrc_vals) > 0 else np.nan
                snrc_s = float(np.std(s_snrc_vals)) if len(s_snrc_vals) > 0 and np.isfinite(s_snrc_vals).all() else np.nan
                
                p_prd_means[s] = prd_m
                p_prdc_means[s] = prdc_m
                p_snrc_means[s] = snrc_m
                
                n_hr_int = len(p_hr_errs_inter[s])
                mae_hr_int = float(np.mean(p_hr_errs_inter[s])) if n_hr_int > 0 else np.nan
                n_hr_ind = len(p_hr_errs_indiv[s])
                mae_hr_ind = float(np.mean(p_hr_errs_indiv[s])) if n_hr_ind > 0 else np.nan
                cov_hr = (n_hr_ind / p_tot_hr[s] * 100.0) if p_tot_hr[s] > 0 else 0.0
                dr_hr_s = float(np.mean(p_hr_dr[s])) if len(p_hr_dr[s]) > 0 else np.nan
                
                n_rr_int = len(p_rr_errs_inter[s])
                mae_rr_int = float(np.mean(p_rr_errs_inter[s])) if n_rr_int > 0 else np.nan
                n_rr_ind = len(p_rr_errs_indiv[s])
                mae_rr_ind = float(np.mean(p_rr_errs_indiv[s])) if n_rr_ind > 0 else np.nan
                cov_rr = (n_rr_ind / p_tot_rr[s] * 100.0) if p_tot_rr[s] > 0 else 0.0
                dr_rr_s = float(np.mean(p_rr_dr[s])) if len(p_rr_dr[s]) > 0 else np.nan
                
                writer.writerow([
                    s, sub_recs_str[s], sub_primary_pid[s], m, len(s_blocks), f"{prd_m:.4f}", f"{prd_s:.4f}",
                    f"{prdc_m:.4f}", f"{prdc_s:.4f}", f"{snrc_m:.4f}", f"{snrc_s:.4f}",
                    n_hr_int, f"{mae_hr_int:.2f}" if not np.isnan(mae_hr_int) else "",
                    n_hr_ind, f"{mae_hr_ind:.2f}" if not np.isnan(mae_hr_ind) else "",
                    f"{cov_hr:.1f}", f"{dr_hr_s:.2f}" if not np.isnan(dr_hr_s) else "",
                    n_rr_int, f"{mae_rr_int:.2f}" if not np.isnan(mae_rr_int) else "",
                    n_rr_ind, f"{mae_rr_ind:.2f}" if not np.isnan(mae_rr_ind) else "",
                    f"{cov_rr:.1f}", f"{dr_rr_s:.2f}" if not np.isnan(dr_rr_s) else ""
                ])
                
            # Tổng hợp toàn bộ bệnh nhân
            p_mean_hr_inter = {s: (float(np.mean(p_hr_errs_inter[s])) if len(p_hr_errs_inter[s]) > 0 else np.nan) for s in subject_ids}
            p_mean_rr_inter = {s: (float(np.mean(p_rr_errs_inter[s])) if len(p_rr_errs_inter[s]) > 0 else np.nan) for s in subject_ids}
            p_mean_hr_indiv = {s: (float(np.mean(p_hr_errs_indiv[s])) if len(p_hr_errs_indiv[s]) > 0 else np.nan) for s in subject_ids}
            p_mean_rr_indiv = {s: (float(np.mean(p_rr_errs_indiv[s])) if len(p_rr_errs_indiv[s]) > 0 else np.nan) for s in subject_ids}
            p_mean_hr_dr = {s: (float(np.mean(p_hr_dr[s])) if len(p_hr_dr[s]) > 0 else np.nan) for s in subject_ids}
            p_mean_rr_dr = {s: (float(np.mean(p_rr_dr[s])) if len(p_rr_dr[s]) > 0 else np.nan) for s in subject_ids}
            
            prd_mean, prd_ci_low, prd_ci_high = bootstrap_subject_ci(p_prd_means)
            prdc_mean, prdc_ci_low, prdc_ci_high = bootstrap_subject_ci(p_prdc_means)
            snrc_mean, snrc_ci_low, snrc_ci_high = bootstrap_subject_ci(p_snrc_means)
            
            hr_inter_mean, hr_inter_low, hr_inter_high = bootstrap_subject_ci(p_mean_hr_inter)
            rr_inter_mean, rr_inter_low, rr_inter_high = bootstrap_subject_ci(p_mean_rr_inter)
            
            hr_indiv_mean, hr_indiv_low, hr_indiv_high = bootstrap_subject_ci(p_mean_hr_indiv)
            rr_indiv_mean, rr_indiv_low, rr_indiv_high = bootstrap_subject_ci(p_mean_rr_indiv)

            dr_hr_mean, dr_hr_low, dr_hr_high = bootstrap_subject_ci(p_mean_hr_dr)
            dr_rr_mean, dr_rr_low, dr_rr_high = bootstrap_subject_ci(p_mean_rr_dr)
            
            total_hr_valid = sum(len(p_hr_errs_indiv[s]) for s in subject_ids)
            total_hr_all = sum(p_tot_hr.values())
            total_rr_valid = sum(len(p_rr_errs_indiv[s]) for s in subject_ids)
            total_rr_all = sum(p_tot_rr.values())
            
            results_summary["methods"][m] = {
                "prd_mean": prd_mean,
                "prd_std": float(np.nanstd(list(p_prd_means.values()))),
                "prd_ci": [prd_ci_low, prd_ci_high],
                "prdc_mean": prdc_mean,
                "prdc_std": float(np.nanstd(list(p_prdc_means.values()))),
                "prdc_ci": [prdc_ci_low, prdc_ci_high],
                "snrc_mean": snrc_mean,
                "snrc_std": float(np.nanstd(list(p_snrc_means.values()))) if m != 'Uncompressed' else np.nan,
                "snrc_ci": [snrc_ci_low, snrc_ci_high],
                # Tập giao hợp lệ (Intersection)
                "intersection": {
                    "mae_hr_mean": hr_inter_mean,
                    "mae_hr_std": float(np.nanstd(list(p_mean_hr_inter.values()))),
                    "mae_hr_ci": [hr_inter_low, hr_inter_high],
                    "mae_rr_mean": rr_inter_mean,
                    "mae_rr_std": float(np.nanstd(list(p_mean_rr_inter.values()))),
                    "mae_rr_ci": [rr_inter_low, rr_inter_high]
                },
                # Mức thay đổi đầu ra Dr so với tín hiệu không nén (Formula 13)
                "dr_hr_mean": dr_hr_mean,
                "dr_hr_std": float(np.nanstd(list(p_mean_hr_dr.values()))),
                "dr_hr_ci": [dr_hr_low, dr_hr_high],
                "dr_rr_mean": dr_rr_mean,
                "dr_rr_std": float(np.nanstd(list(p_mean_rr_dr.values()))),
                "dr_rr_ci": [dr_rr_low, dr_rr_high],
                # Kết quả riêng (Individual)
                "individual": {
                    "mae_hr_mean": hr_indiv_mean,
                    "mae_hr_std": float(np.nanstd(list(p_mean_hr_indiv.values()))),
                    "mae_hr_ci": [hr_indiv_low, hr_indiv_high],
                    "hr_coverage_pct": float(total_hr_valid / total_hr_all * 100.0) if total_hr_all > 0 else np.nan,
                    "mae_rr_mean": rr_indiv_mean,
                    "mae_rr_std": float(np.nanstd(list(p_mean_rr_indiv.values()))),
                    "mae_rr_ci": [rr_indiv_low, rr_indiv_high],
                    "rr_coverage_pct": float(total_rr_valid / total_rr_all * 100.0) if total_rr_all > 0 else np.nan,
                    "rr_failure_pct": float((1.0 - total_rr_valid / total_rr_all) * 100.0) if total_rr_all > 0 else np.nan,
                    "hr_labeled_blocks": total_hr_all, "hr_valid_blocks": total_hr_valid,
                    "rr_labeled_contexts": total_rr_all, "rr_valid_contexts": total_rr_valid,
                    "coverage_aggregation": "Valid windows divided by windows with valid reference labels",
                    "rr_coverage_status": "defined" if total_rr_all else "no_reference_labels"
                },
                "per_patient_hr_intersection": p_mean_hr_inter,
                "per_patient_rr_intersection": p_mean_rr_inter,
                "per_patient_dr_hr": p_mean_hr_dr,
                "per_patient_dr_rr": p_mean_rr_dr
            }
            
    print(f"-> Da xuat ket qua tung benh nhan: {csv_patient_path}")
    
    # -------------------------------------------------------------------------
    # TÍNH CHÊNH LỆCH GHÉP CẶP VỚI TÍN HIỆU GỐC (PAIRED BOOTSTRAP DELTA)
    # -------------------------------------------------------------------------
    uncomp_hr_p = results_summary["methods"]["Uncompressed"]["per_patient_hr_intersection"]
    uncomp_rr_p = results_summary["methods"]["Uncompressed"]["per_patient_rr_intersection"]
    
    for m in methods:
        if m == "Uncompressed":
            results_summary["methods"][m]["delta_mae_hr"] = 0.0
            results_summary["methods"][m]["delta_mae_hr_ci"] = [0.0, 0.0]
            results_summary["methods"][m]["delta_mae_rr"] = 0.0
            results_summary["methods"][m]["delta_mae_rr_ci"] = [0.0, 0.0]
            continue
            
        cur_hr_p = results_summary["methods"][m]["per_patient_hr_intersection"]
        cur_rr_p = results_summary["methods"][m]["per_patient_rr_intersection"]
        
        diff_hr = {s: (cur_hr_p[s] - uncomp_hr_p[s]) for s in subject_ids if not np.isnan(cur_hr_p[s]) and not np.isnan(uncomp_hr_p[s])}
        diff_rr = {s: (cur_rr_p[s] - uncomp_rr_p[s]) for s in subject_ids if not np.isnan(cur_rr_p[s]) and not np.isnan(uncomp_rr_p[s])}
        
        d_hr_mean, d_hr_low, d_hr_high = bootstrap_paired_diff_ci(diff_hr)
        d_rr_mean, d_rr_low, d_rr_high = bootstrap_paired_diff_ci(diff_rr)
        
        results_summary["methods"][m]["delta_mae_hr"] = d_hr_mean
        results_summary["methods"][m]["delta_mae_hr_ci"] = [d_hr_low, d_hr_high]
        results_summary["methods"][m]["delta_mae_rr"] = d_rr_mean
        results_summary["methods"][m]["delta_mae_rr_ci"] = [d_rr_low, d_rr_high]

    valid_subs_rr = [s for s in subject_ids if not np.isnan(results_summary["methods"]["Uncompressed"]["per_patient_rr_intersection"][s])]
    valid_subs_hr = [s for s in subject_ids if not np.isnan(results_summary["methods"]["Uncompressed"]["per_patient_hr_intersection"][s])]
    results_summary["intersection_info"] = {
        "total_test_contexts": len(all_ctx_keys),
        "total_test_blocks": len(all_b_keys),
        "hr_intersection_blocks": len(common_valid_hr_keys),
        "hr_intersection_patients": len(valid_subs_hr),
        "hr_intersection_pct": float(len(common_valid_hr_keys) / len(all_b_keys) * 100.0) if len(all_b_keys) > 0 else 0.0,
        "rr_intersection_contexts": len(common_valid_rr_keys),
        "rr_intersection_patients": len(valid_subs_rr),
        "rr_intersection_pct": float(len(common_valid_rr_keys) / len(all_ctx_keys) * 100.0) if len(all_ctx_keys) > 0 else 0.0,
        "rr_valid_patients_list": valid_subs_rr
    }

    results_summary["boundary_analysis"] = {}
    for m in methods:
        junction_subjects = [ctx['subject_id'] for ctx in non_overlapping_contexts for _ in range(3)]
        subject_amp = [np.mean([v for v, s in zip(boundary_amp_store[m], junction_subjects) if s == subject]) for subject in subject_ids]
        subject_slope = [np.mean([v for v, s in zip(boundary_slope_store[m], junction_subjects) if s == subject]) for subject in subject_ids]
        results_summary["boundary_analysis"][m] = {
            "amp_step_error_mean": float(np.mean(subject_amp)),
            "amp_step_error_std": float(np.std(subject_amp)),
            "slope_error_mean": float(np.mean(subject_slope)),
            "slope_error_std": float(np.std(subject_slope)),
            "aggregation": "Equal mean over source subjects after pooling their within-record junctions",
            "subjects": len(subject_ids), "junctions": len(junction_subjects)
        }

    def label_audit(values, patient_ids, limits, unit):
        values = np.asarray(values, dtype=np.float64)
        valid = np.isfinite(values) & (values > 0)
        outside = valid & ((values < limits[0]) | (values > limits[1]))
        return {"unit": unit, "total": len(values), "valid": int(valid.sum()),
                "missing_or_invalid": int((~valid).sum()),
                "outside_reader_band": int(outside.sum()), "reader_band": list(limits),
                "minimum": float(values[valid].min()) if valid.any() else None,
                "maximum": float(values[valid].max()) if valid.any() else None,
                "valid_patients": len({pid for pid, ok in zip(patient_ids, valid) if ok}),
                "policy": "Finite positive labels retained, including outside-band labels; no imputation"}
    rr_band = config["evaluation"]["rr_estimator"].get("search_band", [0.1, 0.7])
    results_summary["reference_label_audit"] = {
        "hr": label_audit([b["hr_ref"] for b in block_meta], [b["subject_id"] for b in block_meta],
                          [hr_min_bpm, hr_max_bpm], "beats/min"),
        "rr": label_audit([r["rr_ref"] for r in non_overlapping_contexts],
                          [r["subject_id"] for r in non_overlapping_contexts],
                          [60 * rr_band[0], 60 * rr_band[1]], "breaths/min")}
    for method, stats in results_summary['methods'].items():
        stats['snrc_status'] = 'positive_infinity_perfect_reconstruction' if method == 'Uncompressed' else 'finite'
        stats['snrc_ci_status'] = 'not_defined_for_positive_infinity' if method == 'Uncompressed' else 'bootstrap_subjects'
    return results_summary
