import numpy as np
from scipy.signal import find_peaks, detrend, welch

def rr_options(config):
    """Use all locked reader options, not merely search-band fields."""
    source = config['evaluation']['rr_estimator']
    keys = ['min_coverage_sec', 'max_discrepancy', 'search_band', 'prominence_factor',
            'min_peaks', 'min_troughs', 'min_paired', 'min_unique',
            'welch_window_sec', 'overlap_ratio', 'nfft']
    return dict(target_fs=source['sampling_rate'], **{k: source[k] for k in keys})

def estimate_hr_peaks(ppg_1d: np.ndarray, fs: int = 125, min_peaks: int = 3,
                      min_bpm: float = 30.0, max_bpm: float = 210.0,
                      prominence_factor: float = 0.3) -> float:
    """
    Bộ đọc nhịp tim HR theo Đề cương C5 (Mục 4.2):
    - Phát hiện đỉnh nhịp mạch, yêu cầu ít nhất 3 đỉnh (min_peaks = 3).
    - HR_est = 60 / median(p_{i+1} - p_i) với khoảng nhịp tính bằng giây.
    - Dải vận hành chuẩn: 30 - 210 nhịp/phút.
    """
    std_val = float(np.std(ppg_1d))
    if std_val < 1e-4 or not np.isfinite(std_val):
        return np.nan
        
    min_distance = max(int((60.0 / max_bpm) * fs), 1)
    prominence = prominence_factor * std_val
    peaks, _ = find_peaks(ppg_1d, distance=min_distance, prominence=prominence)
    
    if len(peaks) < min_peaks:
        return np.nan
        
    intervals_sec = np.diff(peaks) / float(fs)
    median_interval = float(np.median(intervals_sec))
    if median_interval <= 0:
        return np.nan
        
    hr_bpm = 60.0 / median_interval
    if min_bpm <= hr_bpm <= max_bpm:
        return float(hr_bpm)
    return np.nan

def estimate_rr_riiv_riav(ppg_32s: np.ndarray, fs: int = 125, 
                          target_fs: float = 4.0, min_coverage_sec: float = 24.0,
                          max_discrepancy: float = 3.0,
                          search_band: tuple = (0.1, 0.7),
                          prominence_factor: float = 0.25,
                          min_peaks: int = 8, min_troughs: int = 8,
                          min_paired: int = 8, min_unique: int = 6,
                          welch_window_sec: float = 16.0,
                          overlap_ratio: float = 0.5,
                          nfft: int = 2048) -> tuple[float, dict]:
    """
    Bộ đọc nhịp thở RR theo Đề cương C5 (Mục 4.2):
    - Trích xuất RIIV (trung bình đỉnh - đáy) và RIAV (hiệu đỉnh - đáy).
    - Nội suy tuyến tính lên lưới 4 Hz trong phạm vi được bao phủ (tối thiểu 24s).
    - Dải tìm kiếm phổ: 0.1 - 0.7 Hz (6 - 42 nhịp thở/phút).
    - Giá trị RR là trung vị (hoặc trung bình 2 giá trị) khi chênh lệch <= 3.0 brpm.
    """
    status_dict = {
        "rr_riiv": np.nan,
        "rr_riav": np.nan,
        "valid": False,
        "coverage_sec": 0.0,
        "discrepancy": np.nan,
        "interpolation": "linear",
        "grid_points": 0,
        "welch_nperseg": 0,
        "welch_nfft": 0,
        "feature_max_gap_sec": np.nan,
        "supported_high_hz": np.nan,
        "failure_reason": "insufficient_features"
    }
    
    std_val = float(np.std(ppg_32s))
    if std_val < 1e-4 or not np.isfinite(std_val):
        status_dict["failure_reason"] = "flat_or_nonfinite_signal"
        return np.nan, status_dict

    min_dist_peaks = max(int((60.0 / 210.0) * fs), 1)
    prominence = prominence_factor * std_val
    peaks, _ = find_peaks(ppg_32s, distance=min_dist_peaks, prominence=prominence)
    troughs, _ = find_peaks(-ppg_32s, distance=min_dist_peaks, prominence=prominence)
    
    if len(peaks) < min_peaks or len(troughs) < min_troughs:
        status_dict["failure_reason"] = "insufficient_features"
        return np.nan, status_dict
        
    paired_times = []
    riiv_vals = []
    riav_vals = []
    
    max_pt_dist_samples = int(fs * 1.0)
    for p in peaks:
        dist = np.abs(troughs - p)
        nearest_idx = int(np.argmin(dist))
        t = troughs[nearest_idx]
        if abs(p - t) <= max_pt_dist_samples:
            p_val = float(ppg_32s[p])
            t_val = float(ppg_32s[t])
            mid_time = float(p + t) / (2.0 * float(fs))
            paired_times.append(mid_time)
            riiv_vals.append((p_val + t_val) / 2.0)
            riav_vals.append(abs(p_val - t_val))
            
    if len(paired_times) < min_paired:
        status_dict["failure_reason"] = "insufficient_paired_features"
        return np.nan, status_dict
        
    # Loại bỏ mốc thời gian trùng lặp để lưới nội suy tuyến tính đơn điệu tăng
    unique_times = []
    unique_riiv = []
    unique_riav = []
    last_t = -1.0
    for idx, t_cur in enumerate(paired_times):
        if t_cur > last_t + 1e-5:
            unique_times.append(t_cur)
            unique_riiv.append(riiv_vals[idx])
            unique_riav.append(riav_vals[idx])
            last_t = t_cur
            
    if len(unique_times) < min_unique:
        status_dict["failure_reason"] = "insufficient_unique_features"
        return np.nan, status_dict
        
    t_start = unique_times[0]
    t_end = unique_times[-1]
    coverage_sec = t_end - t_start
    status_dict["coverage_sec"] = float(coverage_sec)
    max_gap = float(np.max(np.diff(unique_times)))
    supported_high = min(float(search_band[1]), 0.5 / max_gap)
    status_dict["feature_max_gap_sec"] = max_gap
    status_dict["supported_high_hz"] = supported_high
    if supported_high <= float(search_band[0]):
        status_dict["failure_reason"] = "insufficient_beat_density"
        return np.nan, status_dict
    
    if coverage_sec < min_coverage_sec:
        status_dict["failure_reason"] = "insufficient_coverage"
        return np.nan, status_dict
        
    grid_t = np.arange(t_start, t_end, 1.0 / target_fs)
    status_dict["grid_points"] = int(len(grid_t))
    min_points = int(min_coverage_sec * target_fs)
    if len(grid_t) < min_points:
        status_dict["failure_reason"] = "insufficient_grid_points"
        return np.nan, status_dict
        
    # Nội suy tuyến tính đúng giao thức, chỉ trong miền thời gian được đặc trưng bao phủ
    interp_riiv = np.interp(grid_t, unique_times, unique_riiv)
    interp_riav = np.interp(grid_t, unique_times, unique_riav)
    
    def extract_welch_peak(signal_grid: np.ndarray) -> tuple[float, dict]:
        sig_detrend = detrend(signal_grid)
        n_samples = len(sig_detrend)
        nperseg = min(n_samples, int(welch_window_sec * target_fs))  # Khung 16s nếu đủ dài
        if nperseg < int(8.0 * target_fs):
            nperseg = n_samples
        noverlap = int(nperseg * overlap_ratio)
        
        freqs, psd = welch(
            sig_detrend,
            fs=target_fs,
            window='hann',
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft
        )
        welch_meta = {"nperseg": int(nperseg), "nfft": int(nfft)}
        
        low_f, high_f = float(search_band[0]), supported_high
        mask = (freqs >= low_f) & (freqs <= high_f)
        if not np.any(mask):
            return np.nan, welch_meta
            
        freqs_band = freqs[mask]
        psd_band = psd[mask]
        peak_idx = int(np.argmax(psd_band))
        best_freq = float(freqs_band[peak_idx])
        # Nội suy parabol cục bộ quanh bin cực đại
        if 0 < peak_idx < len(psd_band) - 1:
            y0, y1, y2 = psd_band[peak_idx - 1:peak_idx + 2]
            denom = float(y0 - 2.0 * y1 + y2)
            if abs(denom) > np.finfo(float).eps:
                delta = float(np.clip(0.5 * (y0 - y2) / denom, -1.0, 1.0))
                best_freq += delta * float(freqs_band[1] - freqs_band[0])
        return float(best_freq * 60.0), welch_meta
        
    rr_riiv, welch_meta = extract_welch_peak(interp_riiv)
    rr_riav, _ = extract_welch_peak(interp_riav)
    status_dict["welch_nperseg"] = welch_meta["nperseg"]
    status_dict["welch_nfft"] = welch_meta["nfft"]
    
    status_dict["rr_riiv"] = rr_riiv
    status_dict["rr_riav"] = rr_riav
    
    if np.isnan(rr_riiv) or np.isnan(rr_riav):
        status_dict["failure_reason"] = "welch_peak_extraction_failed"
        return np.nan, status_dict
        
    disc = abs(rr_riiv - rr_riav)
    status_dict["discrepancy"] = float(disc)
    
    if disc <= max_discrepancy:
        rr_final = (rr_riiv + rr_riav) / 2.0
        status_dict["valid"] = True
        status_dict["failure_reason"] = ""
        return float(rr_final), status_dict
        
    status_dict["failure_reason"] = "consensus_exceeded"
    return np.nan, status_dict
