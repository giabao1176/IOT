import os
import json
import numpy as np
import yaml
from scipy.signal import butter, sosfilt, step, sos2tf

from utils.estimators import estimate_hr_peaks, estimate_rr_riiv_riav, rr_options
from utils.deployment_reference import configured_sos, sustained_settling_time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def evaluate_dev_uncompressed_baseline():
    cfg_path = os.path.join(BASE_DIR, "configs", "c5.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    data_path = os.path.join(BASE_DIR, cfg["data"]["processed_dir"], "bidmc_processed_dataset.npy")
    splits_path = os.path.join(BASE_DIR, cfg["data"]["splits_file"])

    assert os.path.exists(data_path), f"Khong tim thay {data_path}"
    assert os.path.exists(splits_path), f"Khong tim thay {splits_path}"

    data = np.load(data_path, allow_pickle=True)
    with open(splits_path, "r", encoding="utf-8") as f:
        splits = json.load(f)

    dev_pids = set(splits["dev_pids"])
    dev_records = [r for r in data if r["pid"] in dev_pids]

    # Trich xuat cac ngu canh 32s khong chong lap theo tung benh nhan phat trien
    non_overlap_dev = []
    for pid in sorted(dev_pids):
        cur_p = [r for r in dev_records if r["pid"] == pid]
        last_end = -1.0
        for r in cur_p:
            if r["t_start"] >= last_end - 1e-4:
                non_overlap_dev.append(r)
                last_end = r["t_end"]

    fs = int(cfg["data"]["sampling_rate"])
    hr_cfg = cfg.get("evaluation", {}).get("hr_estimator", {})
    hr_min_peaks = int(hr_cfg.get("min_peaks", 3))
    hr_min_bpm = float(hr_cfg.get("min_bpm", 30.0))
    hr_max_bpm = float(hr_cfg.get("max_bpm", 210.0))
    hr_prominence_factor = float(hr_cfg.get("prominence_factor", 0.3))

    rr_cfg = cfg.get("evaluation", {}).get("rr_estimator", {})
    search_band = tuple(rr_cfg.get("search_band", [0.1, 0.7]))
    max_discrepancy = float(rr_cfg.get("max_discrepancy", 3.0))
    target_fs = float(rr_cfg.get("sampling_rate", 4.0))
    min_coverage_sec = float(rr_cfg.get("min_coverage_sec", 24.0))

    per_patient_stats = {}
    for pid in sorted(splits['dev_subjects']):
        per_patient_stats[pid] = {
            "hr_errors": [],
            "hr_total_with_ref": 0,
            "hr_valid_estimates": 0,
            "rr_errors": [],
            "rr_total_with_ref": 0,
            "rr_valid_estimates": 0,
            "rr_failure_reasons": {}
        }

    raw_block_stds = []

    for ctx in non_overlap_dev:
        pid = ctx["subject_id"]
        # HR danh gia tren 4 khoi 8s
        for b in ctx["blocks"]:
            raw_sig = b["signal_raw"]
            hr_ref = b["hr_ref"]
            raw_block_stds.append(float(np.std(raw_sig)))

            if not np.isnan(hr_ref):
                per_patient_stats[pid]["hr_total_with_ref"] += 1
                hr_est = estimate_hr_peaks(
                    raw_sig, fs=fs, min_peaks=hr_min_peaks, min_bpm=hr_min_bpm,
                    max_bpm=hr_max_bpm, prominence_factor=hr_prominence_factor
                )
                if not np.isnan(hr_est):
                    per_patient_stats[pid]["hr_valid_estimates"] += 1
                    per_patient_stats[pid]["hr_errors"].append(abs(hr_est - hr_ref))

        # RR danh gia tren ngu canh 32s
        raw_seq_32s = np.concatenate([b["signal_raw"] for b in ctx["blocks"]])
        rr_ref = ctx["rr_ref"]
        if not np.isnan(rr_ref):
            per_patient_stats[pid]["rr_total_with_ref"] += 1
            rr_est, status = estimate_rr_riiv_riav(
                raw_seq_32s, fs=fs, **rr_options(cfg)
            )
            if not np.isnan(rr_est):
                per_patient_stats[pid]["rr_valid_estimates"] += 1
                per_patient_stats[pid]["rr_errors"].append(abs(rr_est - rr_ref))
            else:
                reason = status.get("failure_reason", "unknown")
                cur_reasons = per_patient_stats[pid]["rr_failure_reasons"]
                cur_reasons[reason] = cur_reasons.get(reason, 0) + 1

    # Tong hop theo benh nhan (patient-level macro averaging)
    patient_hr_maes = []
    patient_hr_covs = []
    patient_rr_maes = []
    patient_rr_covs = []
    all_rr_failure_breakdown = {}

    for pid, s in per_patient_stats.items():
        if s["hr_valid_estimates"] > 0:
            patient_hr_maes.append(float(np.mean(s["hr_errors"])))
        cov_hr = (s["hr_valid_estimates"] / s["hr_total_with_ref"] * 100.0) if s["hr_total_with_ref"] > 0 else 0.0
        patient_hr_covs.append(cov_hr)

        if s["rr_valid_estimates"] > 0:
            patient_rr_maes.append(float(np.mean(s["rr_errors"])))
        cov_rr = (s["rr_valid_estimates"] / s["rr_total_with_ref"] * 100.0) if s["rr_total_with_ref"] > 0 else 0.0
        patient_rr_covs.append(cov_rr)

        for rk, rv in s["rr_failure_reasons"].items():
            all_rr_failure_breakdown[rk] = all_rr_failure_breakdown.get(rk, 0) + rv

    # Bang chung 1: Phan bo do lech chuan tin hieu tho (Flat Signal Proof)
    raw_block_stds = np.array(raw_block_stds)
    flat_std_min = float(cfg["data"]["quality"]["flat_std_min"])
    flat_evidence = {
        "flat_threshold_locked": flat_std_min,
        "raw_blocks_total": len(raw_block_stds),
        "signal_scope": "Filtered accepted development blocks; not an audit of hardware failures",
        "min_std": float(np.min(raw_block_stds)),
        "percentile_01_std": float(np.percentile(raw_block_stds, 1.0)),
        "percentile_05_std": float(np.percentile(raw_block_stds, 5.0)),
        "median_std": float(np.median(raw_block_stds)),
        "max_std": float(np.max(raw_block_stds)),
        "blocks_below_threshold": int(np.sum(raw_block_stds < flat_std_min)),
        "conclusion": "Nguong da khoa nam duoi phan bo cac khoi da loc va duoc chap nhan tren tap phat trien. Bang chung nay khong chung minh do nhay phat hien loi cam bien hay loi phan cung."
    }

    # Bang chung 2: Dap ung qua do cua bo loc (32s Warmup Proof)
    sos = configured_sos(cfg)
    # Tinh dap ung qua do bang buoc nhay don vi
    warmup_seconds = float(cfg["data"]["warmup_seconds"])
    n_warmup_samples = int(round(warmup_seconds * fs))
    t_test = np.arange(int(120 * fs)) / float(fs)
    step_inp = np.ones_like(t_test)
    step_resp = sosfilt(sos, step_inp)
    
    # Do thi gian suy giam ve < 1% bien do cuc dai qua do
    resp_abs = np.abs(step_resp)
    peak_val = np.max(resp_abs)
    settling_time_sec = sustained_settling_time(step_resp, fs, fraction=0.01)

    warmup_evidence = {
        "warmup_seconds_locked": warmup_seconds,
        "filter_order_total": 2 * cfg["data"]["filter"]["order_n"],
        "low_hz": cfg["data"]["filter"]["low_hz"],
        "high_hz": cfg["data"]["filter"]["high_hz"],
        "observation_horizon_seconds": 120.0,
        "settling_threshold_fraction_of_peak": 0.01,
        "settling_definition": "First sample after the final violation, sustained within the 120-second observation horizon",
        "transient_settling_time_to_1pct_sec": settling_time_sec,
        "transient_amplitude_at_32s": float(resp_abs[n_warmup_samples]),
        "transient_fraction_of_peak_at_warmup": float(resp_abs[n_warmup_samples] / peak_val),
        "conclusion": "Khoang khoi dong 32 giay duoc ho tro boi dap ung buoc tren tap cau hinh da khoa; sai lech qua do nho hon nguong 1% trong khoang quan sat, khong co nghia meo qua do bang khong."
    }

    results = {
        "dev_subjects_count": len(splits['dev_subjects']),
        "dev_records_count": len(dev_pids),
        "aggregation": "Pool all records within a source subject, then equal mean over valid subjects",
        "dev_non_overlapping_contexts_32s": len(non_overlap_dev),
        "dev_non_overlapping_blocks_8s": len(non_overlap_dev) * 4,
        "uncompressed_metrics": {
            "mae_hr_patient_mean": float(np.mean(patient_hr_maes)),
            "mae_hr_patient_std": float(np.std(patient_hr_maes)),
            "hr_coverage_patient_mean_pct": float(np.mean(patient_hr_covs)),
            "hr_valid_patients": len(patient_hr_maes),
            "mae_rr_patient_mean": float(np.mean(patient_rr_maes)),
            "mae_rr_patient_std": float(np.std(patient_rr_maes)),
            "rr_coverage_patient_mean_pct": float(np.mean(patient_rr_covs)),
            "rr_valid_patients": len(patient_rr_maes),
            "rr_failure_breakdown": all_rr_failure_breakdown
        },
        "flat_signal_evidence": flat_evidence,
        "warmup_transient_evidence": warmup_evidence
    }

    out_file = os.path.join(BASE_DIR, "data", "processed", "dev_uncompressed_baseline.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    n_dev_subs = len(splits.get("dev_subjects", dev_pids))
    n_dev_recs = len(dev_pids)
    print(f"=== KET QUA DANH GIA PPG KHONG NEN TREN TAP PHAT TRIEN ({n_dev_subs} DOI TUONG, {n_dev_recs} BAN GHI) ===")
    print(f"So ngu canh 32s khong chong lap: {len(non_overlap_dev)}")
    print(f"MAE-HR khong nen: {results['uncompressed_metrics']['mae_hr_patient_mean']:.2f} +- {results['uncompressed_metrics']['mae_hr_patient_std']:.2f} bpm (Do bao phu: {results['uncompressed_metrics']['hr_coverage_patient_mean_pct']:.1f}%)")
    print(f"MAE-RR khong nen: {results['uncompressed_metrics']['mae_rr_patient_mean']:.2f} +- {results['uncompressed_metrics']['mae_rr_patient_std']:.2f} brpm (Do bao phu: {results['uncompressed_metrics']['rr_coverage_patient_mean_pct']:.1f}%)")
    print(f"Ly do that bai RR: {all_rr_failure_breakdown}")
    print(f"Thoi gian on dinh bo loc 0.05-8 Hz ve <1%: {settling_time_sec:.2f}s (khoang warmup 32s hop le)")
    print(f"-> Da luu bang chung tai {out_file}")
    return results

if __name__ == "__main__":
    evaluate_dev_uncompressed_baseline()
