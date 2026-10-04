import os
import json
import glob
import re
import numpy as np
import pandas as pd
from scipy.signal import butter, sosfilt
from sklearn.model_selection import GroupKFold
import wfdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_fix_info(raw_dir: str) -> dict:
    subject_map = {}
    for pid in range(1, 54):
        fix_path = os.path.join(raw_dir, f"bidmc_{pid:02d}_Fix.txt")
        if not os.path.exists(fix_path):
            raise RuntimeError(f"Tệp định danh Fix bắt buộc không tồn tại: {fix_path}")
        mimic_id = None
        with open(fix_path, 'r', encoding='utf-8', errors='ignore') as fp:
            for line in fp:
                if 'MIMIC II matched wdb ID:' in line:
                    val = line.split(':')[-1].strip()
                    if val:
                        mimic_id = val
                        break
        if not mimic_id:
            raise RuntimeError(f"Không thể đọc mã MIMIC II từ tệp {fix_path}; không tự tạo mã thay thế")
        subject_map[pid] = mimic_id
    return subject_map

def extract_hr_ref(numerics_df: pd.DataFrame, t_start: float, t_end: float, 
                   min_valid: int = 6) -> float:
    # Cột Time [s] và HR
    time_col = [c for c in numerics_df.columns if 'time' in c.lower()][0]
    hr_col = [c for c in numerics_df.columns if 'hr' in c.lower()][0]
    
    mask = (numerics_df[time_col] >= t_start) & (numerics_df[time_col] < t_end)
    sub = numerics_df.loc[mask, hr_col]
    vals = pd.to_numeric(sub, errors='coerce').dropna()
    vals = vals[np.isfinite(vals) & (vals > 0)]
    if len(vals) >= min_valid:
        return float(np.median(vals))
    return np.nan

def extract_rr_ref(breaths_df: pd.DataFrame, t_start: float, t_end: float,
                   fs: int = 125, max_diff: float = 2.0) -> float:
    s_start = int(t_start * fs)
    s_end = int(t_end * fs)
    
    col1 = breaths_df.columns[0]
    col2 = breaths_df.columns[1]
    
    b1 = pd.to_numeric(breaths_df[col1], errors='coerce').dropna().values
    b2 = pd.to_numeric(breaths_df[col2], errors='coerce').dropna().values
    
    m1 = b1[(b1 >= s_start) & (b1 < s_end)]
    m2 = b2[(b2 >= s_start) & (b2 < s_end)]
    
    def calc_rate(marks):
        # Đề cương C5 yêu cầu ít nhất 3 mốc thở cho mỗi người chú thích trong cửa sổ 32 giây
        if len(marks) < 3:
            return np.nan
        dur_sec = (marks[-1] - marks[0]) / float(fs)
        if dur_sec <= 0.0:
            return np.nan
        cycles = len(marks) - 1
        rate = (cycles / dur_sec) * 60.0
        return rate
        
    r1 = calc_rate(m1)
    r2 = calc_rate(m2)
    
    if np.isnan(r1) or np.isnan(r2):
        return np.nan
    # Ngưỡng đồng thuận giữa 2 người chú thích (mặc định 2.0 brpm)
    if abs(r1 - r2) <= max_diff:
        return float((r1 + r2) / 2.0)
    return np.nan

def process_all_bidmc(raw_dir: str = None, 
                      processed_dir: str = None,
                      splits_path: str = None,
                      seed: int = None,
                      config: dict = None):
    data_cfg = (config or {}).get("data", {})
    if seed is None:
        seed = int((config or {}).get("project", {}).get("seed", 2026))
    if raw_dir is None:
        raw_dir = os.path.join(BASE_DIR, data_cfg.get("raw_dir", "data/raw_bidmc"))
    if processed_dir is None:
        processed_dir = os.path.join(BASE_DIR, data_cfg.get("processed_dir", "data/processed"))
    if splits_path is None:
        splits_path = os.path.join(BASE_DIR, data_cfg.get("splits_file", "data/splits_subject.json"))
        
    os.makedirs(processed_dir, exist_ok=True)
    os.makedirs(os.path.dirname(splits_path), exist_ok=True)
    
    print(f"Raw dir: {raw_dir}")
    print(f"Processed dir: {processed_dir}")
    
    print("\n1. Kiem tra danh sach benh nhan va thong tin Fix tu MIMIC II...")
    subject_map = load_fix_info(raw_dir)
    all_pids = sorted(list(subject_map.keys()))
    if len(all_pids) != 53:
        raise RuntimeError(f"Yêu cầu đầy đủ 53 bản ghi BIDMC, hiện có {len(all_pids)}")
    
    unique_subjects = sorted(list(set(subject_map.values())))
    print(f"Tong so ban ghi: {len(all_pids)}, Tong so doi tuong nguon MIMIC: {len(unique_subjects)}")
    
    sub_to_records = {}
    for r, s in subject_map.items():
        sub_to_records.setdefault(s, []).append(r)
    for s in sub_to_records:
        sub_to_records[s] = sorted(sub_to_records[s])
        
    # Danh sách 10 bệnh nhân kiểm thử cố định theo seed 2026 ban đầu:
    # Bản ghi 23 (thuộc s11342) cùng 20, 21, 22 được gộp trọn vẹn vào tập kiểm thử.
    orig_test_recs = [11, 12, 19, 23, 31, 37, 42, 43, 45, 48]
    test_subjects = sorted(list(set(subject_map[r] for r in orig_test_recs)))
    test_records = sorted([r for s in test_subjects for r in sub_to_records[s]])
    
    dev_subjects = sorted([s for s in unique_subjects if s not in test_subjects])
    dev_records = sorted([r for s in dev_subjects for r in sub_to_records[s]])
    
    # Kiểm tra tính độc lập tuyệt đối giữa dev và test
    assert len(test_subjects) == 10, f"Kỳ vọng 10 test subjects, nhận {len(test_subjects)}"
    assert len(test_records) == 13, f"Kỳ vọng 13 test records, nhận {len(test_records)}"
    assert len(dev_subjects) == 36, f"Kỳ vọng 36 dev subjects, nhận {len(dev_subjects)}"
    assert len(dev_records) == 40, f"Kỳ vọng 40 dev records, nhận {len(dev_records)}"
    assert len(set(test_subjects).intersection(dev_subjects)) == 0, "Rò rỉ bệnh nhân giữa dev và test!"
    assert len(set(test_records).intersection(dev_records)) == 0, "Rò rỉ bản ghi giữa dev và test!"
    
    # Chia 5 nếp kiểm định trên 36 đối tượng phát triển bằng GroupKFold
    rng = np.random.RandomState(seed)
    shuffled_dev_subs = rng.permutation(dev_subjects).tolist()
    sub_order = {s: i for i, s in enumerate(shuffled_dev_subs)}
    dev_recs_ordered = sorted(dev_records, key=lambda r: (sub_order[subject_map[r]], r))
    groups = [sub_order[subject_map[r]] for r in dev_recs_ordered]
    
    folds = {}
    n_splits = int((config or {}).get("evaluation", {}).get("k_folds", 5))
    gkf = GroupKFold(n_splits=n_splits)
    for f_idx, (tr_idx, val_idx) in enumerate(gkf.split(dev_recs_ordered, groups=groups), start=1):
        tr_recs = sorted([dev_recs_ordered[i] for i in tr_idx])
        val_recs = sorted([dev_recs_ordered[i] for i in val_idx])
        tr_subs = sorted(list(set(subject_map[r] for r in tr_recs)))
        val_subs = sorted(list(set(subject_map[r] for r in val_recs)))
        
        # Bắt buộc không trùng mã nguồn MIMIC giữa hai phía nếp kiểm định
        inter = set(tr_subs).intersection(val_subs)
        if len(inter) > 0:
            raise RuntimeError(f"Rò rỉ đối tượng nguồn MIMIC tại fold {f_idx}: {inter}")
            
        folds[f"fold_{f_idx}"] = {
            "train_subjects": tr_subs,
            "val_subjects": val_subs,
            "train_records": tr_recs,
            "val_records": val_recs,
            "train_pids": tr_recs, # Giữ để tương thích ngược
            "val_pids": val_recs   # Giữ để tương thích ngược
        }
        
    splits_data = {
        "seed": seed,
        "total_subjects": len(unique_subjects),
        "total_records": len(all_pids),
        "test_subjects_count": len(test_subjects),
        "test_records_count": len(test_records),
        "dev_subjects_count": len(dev_subjects),
        "dev_records_count": len(dev_records),
        "test_subjects": test_subjects,
        "dev_subjects": dev_subjects,
        "test_records": test_records,
        "dev_records": dev_records,
        "test_pids": test_records, # Giữ để tương thích ngược
        "dev_pids": dev_records,   # Giữ để tương thích ngược
        "subject_to_records": sub_to_records,
        "record_to_subject": subject_map,
        "folds": folds
    }
    with open(splits_path, 'w', encoding='utf-8') as f:
        json.dump(splits_data, f, indent=2)
    print(f"-> Da luu thiet lap chia tap Subject-wise chuan tai {splits_path}")
    print(f"Test Subjects (10): {test_subjects}")
    print(f"Test Records (13): {test_records}")
    print(f"Dev Subjects (36), Dev Records (40)")

    
    # Khoi tao bo loc Butterworth thong dai 0.05 - 8.0 Hz bac N=2 (tong bac 4)
    fs = int(data_cfg.get("sampling_rate", 125))
    filter_cfg = data_cfg.get("filter", {})
    sos = butter(
        N=int(filter_cfg.get("order_n", 2)),
        Wn=[float(filter_cfg.get("low_hz", 0.05)), float(filter_cfg.get("high_hz", 8.0))],
        btype='bandpass', fs=fs, output='sos'
    )
    context_samples = int(data_cfg.get("context_samples", 32 * fs))
    block_samples = int(data_cfg.get("window_samples", 8 * fs))
    train_step_samples = int(data_cfg.get("step_samples", 4 * fs))
    warmup_seconds = float(data_cfg.get("warmup_seconds", 32))
    norm_eps = float(data_cfg.get("normalization", {}).get("eps", 1e-6))
    quality_cfg = data_cfg.get("quality", {})
    require_finite = bool(quality_cfg.get("require_finite", True))
    flat_std_min = float(quality_cfg.get("flat_std_min", 1e-4))
    label_cfg = data_cfg.get("label_rules", {})
    hr_min_valid = int(label_cfg.get("hr_min_valid_points", 6))
    rr_max_diff = float(label_cfg.get("rr_max_consensus_diff", 2.0))
    
    dataset_records = []
    total_contexts = 0
    total_blocks = 0
    audit = {
        "records_expected": len(all_pids),
        "records_processed": 0,
        "records_missing_files": 0,
        "raw_nonfinite_samples": 0,
        "candidate_contexts": 0,
        "rejected_nonfinite_or_missing": 0,
        "rejected_flat_signal": 0,
        "accepted_contexts": 0,
        "accepted_blocks": 0,
        "contexts_missing_rr_label": 0,
        "blocks_missing_hr_label": 0,
        "flat_std_min": flat_std_min,
        "require_finite": require_finite,
        "per_patient": {}
    }
    
    print("\n2. Bat dau loc tin hieu, phan doan va trich xuat nhan...")
    for pid in all_pids:
        rec_name = f"bidmc{pid:02d}"
        rec_prefix = f"bidmc_{pid:02d}"
        dat_path = os.path.join(raw_dir, f"{rec_name}")
        num_path = os.path.join(raw_dir, f"{rec_prefix}_Numerics.csv")
        br_path = os.path.join(raw_dir, f"{rec_prefix}_Breaths.csv")
        
        if not os.path.exists(dat_path + ".dat"):
            print(f"Missing dat: {dat_path}")
            audit["records_missing_files"] += 1
            continue
        if not os.path.exists(num_path):
            print(f"Missing num: {num_path}")
            audit["records_missing_files"] += 1
            continue
        if not os.path.exists(br_path):
            print(f"Missing br: {br_path}")
            audit["records_missing_files"] += 1
            continue
            
        record = wfdb.rdrecord(dat_path)
        sig_names = [s.strip().replace(',', '') for s in record.sig_name]
        try:
            ppg_idx = sig_names.index('PLETH')
        except ValueError:
            ppg_idx = 1
            
        raw_ppg = record.p_signal[:, ppg_idx]
        warmup_samples = int(round(warmup_seconds * fs))
        finite_raw = np.isfinite(raw_ppg)
        audit["raw_nonfinite_samples"] += int((~finite_raw).sum())

        # Lọc riêng từng đoạn hữu hạn liên tục; không nối qua khoảng mất dữ liệu.
        filtered_ppg = np.full(raw_ppg.shape, np.nan, dtype=np.float64)
        padded = np.r_[False, finite_raw, False]
        starts = np.flatnonzero(~padded[:-1] & padded[1:])
        ends = np.flatnonzero(padded[:-1] & ~padded[1:])
        for seg_start, seg_end in zip(starts, ends):
            segment = raw_ppg[seg_start:seg_end]
            if len(segment) <= warmup_samples:
                continue
            segment_filtered = sosfilt(sos, segment)
            segment_filtered[:warmup_samples] = np.nan
            filtered_ppg[seg_start:seg_end] = segment_filtered

        # Giữ mốc thời gian như giao thức cũ; warm-up đầu bản ghi đã bị đánh dấu NaN.
        valid_ppg = filtered_ppg[warmup_samples:]
        time_offset = warmup_seconds
        audit["records_processed"] += 1
        patient_audit = {
            "candidate_contexts": 0,
            "rejected_nonfinite_or_missing": 0,
            "rejected_flat_signal": 0,
            "accepted_contexts": 0,
            "contexts_missing_rr_label": 0,
            "blocks_missing_hr_label": 0
        }
        
        numerics_df = pd.read_csv(num_path)
        breaths_df = pd.read_csv(br_path)
        
        # Phan doan 32 giay (4000 mau), buoc nhay 4 giay (500 mau)
        ctx_len = context_samples
        step_len = train_step_samples
        block_len = block_samples
        
        n_ctx = (len(valid_ppg) - ctx_len) // step_len + 1
        audit["candidate_contexts"] += max(n_ctx, 0)
        patient_audit["candidate_contexts"] = max(n_ctx, 0)
        
        for c_idx in range(n_ctx):
            s_idx = c_idx * step_len
            e_idx = s_idx + ctx_len
            ctx_signal = valid_ppg[s_idx:e_idx]
            
            t_start = time_offset + (s_idx / float(fs))
            t_end = t_start + (ctx_len / float(fs))
            
            if len(ctx_signal) != ctx_len or (require_finite and not np.all(np.isfinite(ctx_signal))):
                audit["rejected_nonfinite_or_missing"] += 1
                patient_audit["rejected_nonfinite_or_missing"] += 1
                continue

            block_views = [ctx_signal[b * block_len:(b + 1) * block_len] for b in range(4)]
            if (np.std(ctx_signal) < flat_std_min
                    or any(len(block) != block_len or np.std(block) < flat_std_min for block in block_views)):
                audit["rejected_flat_signal"] += 1
                patient_audit["rejected_flat_signal"] += 1
                continue
                
            rr_ref = extract_rr_ref(breaths_df, t_start, t_end, fs=fs, max_diff=rr_max_diff)
            if np.isnan(rr_ref):
                audit["contexts_missing_rr_label"] += 1
                patient_audit["contexts_missing_rr_label"] += 1
            
            # 4 khoi 8s khong chong lap ben trong ngu canh 32s
            blocks_data = []
            for b in range(4):
                b_start_sample = s_idx + b * block_len
                b_end_sample = b_start_sample + block_len
                block_sig = valid_ppg[b_start_sample:b_end_sample]
                
                b_t_start = time_offset + (b_start_sample / float(fs))
                b_t_end = b_t_start + (block_len / float(fs))
                
                mu = float(np.mean(block_sig))
                s = float(np.std(block_sig))
                s_safe = max(s, norm_eps)
                
                block_norm = (block_sig - mu) / s_safe
                hr_ref = extract_hr_ref(numerics_df, b_t_start, b_t_end, min_valid=hr_min_valid)
                if np.isnan(hr_ref):
                    audit["blocks_missing_hr_label"] += 1
                    patient_audit["blocks_missing_hr_label"] += 1
                
                blocks_data.append({
                    "block_idx": b,
                    "signal_raw": block_sig.astype(np.float32),
                    "signal_norm": block_norm.astype(np.float32),
                    "mu": mu,
                    "s": s_safe,
                    "hr_ref": hr_ref,
                    "t_start": b_t_start,
                    "t_end": b_t_end
                })
                
            dataset_records.append({
                "pid": pid,
                "record_id": pid,
                "subject_id": subject_map[pid],
                "context_idx": c_idx,
                "t_start": t_start,
                "t_end": t_end,
                "rr_ref": rr_ref,
                "blocks": blocks_data,
                "is_test": (pid in test_records)
            })
            total_contexts += 1
            total_blocks += 4
            audit["accepted_contexts"] += 1
            audit["accepted_blocks"] += 4
            patient_audit["accepted_contexts"] += 1

        audit["per_patient"][str(pid)] = patient_audit
            
        if pid % 10 == 0 or pid == 53:
            print(f"Da xu ly benh nhan {pid:02d}/53 (hien tai {total_contexts} ngu canh 32s)...")
            
    out_data_path = os.path.join(processed_dir, "bidmc_processed_dataset.npy")
    np.save(out_data_path, dataset_records)
    audit_path = os.path.join(processed_dir, "preprocessing_audit.json")
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)
    print(f"\n-> Hoan tat tien xu ly! Luu tai {out_data_path}")
    print(f"-> Da luu thong ke loai mau tai {audit_path}")
    print(f"Tong so ngu canh 32s: {total_contexts}, Tong so khoi 8s: {total_blocks}")
    return dataset_records

if __name__ == "__main__":
    process_all_bidmc()
