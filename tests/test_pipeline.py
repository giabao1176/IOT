import os
import json
import pytest
import numpy as np
import torch
import torch.nn.functional as F
from scipy.signal import butter, sosfilt

from models.autoencoder import PPGAutoencoder, PPGEncoder, PPGDecoder, simulate_quantization
from models.dct_baseline import DCTBaseline
from utils.packet_codec import (
    compute_crc16, pack_payload, unpack_payload,
    quantize_latent, dequantize_latent, PacketSequenceTracker, run_bit_flip_experiment
)
from utils.estimators import estimate_hr_peaks, estimate_rr_riiv_riav
from utils.spectral_loss import MultiScaleSpectralLoss
from evaluate import calc_prd_prdc_snrc, bootstrap_subject_ci, bootstrap_paired_diff_ci

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# =============================================================================
# CỔNG 1: SO SÁNH TỪNG THÀNH PHẦN MẤT MÁT VỚI PHÉP TÍNH NUMPY ĐỘC LẬP
# =============================================================================
def test_gate1_loss_components_match_numpy_reference():
    fs = 125
    beta = 0.5
    eps_f = 1e-8
    w_hr = 2.0
    w_rr = 4.0

    loss_fn = MultiScaleSpectralLoss(
        fs=fs, beta=beta, hr_band=(0.8, 3.0), hr_weight=w_hr,
        rr_band=(0.1, 0.4), rr_weight=w_rr, eps_f=eps_f
    )

    rng = np.random.RandomState(42)
    B = 2
    # 8 khối 8 giây (2 ngữ cảnh)
    x_raw = rng.randn(B * 4, 1000).astype(np.float32)
    x_hat_raw = x_raw + 0.15 * rng.randn(B * 4, 1000).astype(np.float32)
    stds = rng.uniform(0.5, 3.0, size=(B * 4, 1)).astype(np.float32)

    x_seq = x_raw.reshape(B, 4000)
    x_hat_seq = x_hat_raw.reshape(B, 4000)

    # 1. Phép tính PyTorch
    loss_pt, d_pt = loss_fn(
        torch.tensor(x_raw).unsqueeze(1),
        torch.tensor(x_hat_raw).unsqueeze(1),
        torch.tensor(x_seq),
        torch.tensor(x_hat_seq),
        block_scales=torch.tensor(stds)
    )

    # 2. Phép tính NumPy độc lập
    # Công thức (6): L_time
    diff_norm_np = (x_raw - x_hat_raw) / stds
    loss_time_np = float(np.mean(diff_norm_np ** 2))

    # Công thức (7) & (8): S_8
    h8 = np.hanning(1000).astype(np.float32)
    h8_sum = float(np.sum(h8))
    freqs8 = np.fft.rfftfreq(1000, d=1.0 / fs)
    w8 = np.ones_like(freqs8, dtype=np.float32)
    w8[(freqs8 >= 0.8) & (freqs8 <= 3.0)] = w_hr
    w8[0] = 0.0

    s8_list = []
    for i in range(B * 4):
        v = x_raw[i] - np.mean(x_raw[i])
        v_hat = x_hat_raw[i] - np.mean(x_hat_raw[i])
        A8 = np.abs(np.fft.rfft(v * h8)) / h8_sum
        A8_hat = np.abs(np.fft.rfft(v_hat * h8)) / h8_sum

        A8_k = A8[1:]
        A8_hat_k = A8_hat[1:]
        w8_k = w8[1:]

        num = np.sum(w8_k * (A8_k - A8_hat_k) ** 2)
        den = np.sum(w8_k * (A8_k ** 2)) + eps_f
        s8_list.append(num / den)
    loss_spec8_np = float(np.mean(s8_list))

    # Công thức (7) & (8): S_32
    h32 = np.hanning(4000).astype(np.float32)
    h32_sum = float(np.sum(h32))
    freqs32 = np.fft.rfftfreq(4000, d=1.0 / fs)
    w32 = np.ones_like(freqs32, dtype=np.float32)
    w32[(freqs32 >= 0.1) & (freqs32 <= 0.4)] = w_rr
    w32[0] = 0.0

    s32_list = []
    for i in range(B):
        v = x_seq[i] - np.mean(x_seq[i])
        v_hat = x_hat_seq[i] - np.mean(x_hat_seq[i])
        A32 = np.abs(np.fft.rfft(v * h32)) / h32_sum
        A32_hat = np.abs(np.fft.rfft(v_hat * h32)) / h32_sum

        A32_k = A32[1:]
        A32_hat_k = A32_hat[1:]
        w32_k = w32[1:]

        num = np.sum(w32_k * (A32_k - A32_hat_k) ** 2)
        den = np.sum(w32_k * (A32_k ** 2)) + eps_f
        s32_list.append(num / den)
    loss_spec32_np = float(np.mean(s32_list))

    # Công thức (9): L_spec = 0.5 * (S8 + S32), L = L_time + beta * L_spec
    loss_spec_total_np = 0.5 * (loss_spec8_np + loss_spec32_np)
    loss_total_np = loss_time_np + beta * loss_spec_total_np

    assert abs(d_pt["loss_time"] - loss_time_np) < 1e-6, "Sai lệch thành phần loss_time"
    assert abs(d_pt["loss_spec_8s"] - loss_spec8_np) < 1e-6, "Sai lệch thành phần loss_spec_8s"
    assert abs(d_pt["loss_spec_32s"] - loss_spec32_np) < 1e-6, "Sai lệch thành phần loss_spec_32s"
    assert abs(d_pt["loss_spec_total"] - loss_spec_total_np) < 1e-6, "Sai lệch hệ số 1/2 của L_spec"
    assert abs(loss_pt.item() - loss_total_np) < 1e-6, "Sai lệch tổng mất mát L"


# =============================================================================
# CỔNG 2: MẤT MÁT THỜI GIAN VỚI CÁC KHỐI BIÊN ĐỘ KHÁC NHAU VÀ GRADIENT FLOW
# =============================================================================
def test_gate2_time_loss_varying_scales_and_gradients():
    loss_fn = MultiScaleSpectralLoss(beta=0.5)

    # 4 khối có độ lệch chuẩn s_j rất khác nhau: 0.1, 1.0, 5.0, 10.0
    stds = torch.tensor([[0.1], [1.0], [5.0], [10.0]], dtype=torch.float32)
    # Lỗi ban đầu cố định delta = 1.0 ở mọi khối
    x = torch.zeros(4, 1, 1000, dtype=torch.float32)
    x_hat = torch.ones(4, 1, 1000, dtype=torch.float32)
    x_seq = x.view(1, 4000)
    x_hat_seq = x_hat.view(1, 4000)

    # Với công thức (6), sai số từng khối được chia cho s_j trước khi bình phương:
    # Khối 1: (1 / 0.1)^2 = 100
    # Khối 2: (1 / 1.0)^2 = 1
    # Khối 3: (1 / 5.0)^2 = 0.04
    # Khối 4: (1 / 10.0)^2 = 0.01
    # Trung bình: (100 + 1 + 0.04 + 0.01) / 4 = 25.2625
    loss, d = loss_fn(x, x_hat, x_seq, x_hat_seq, block_scales=stds)
    expected_time_loss = (100.0 + 1.0 + 0.04 + 0.01) / 4.0
    assert abs(d["loss_time"] - expected_time_loss) < 1e-4

    # Kiểm tra luồng gradient qua mô hình tự mã hóa
    model = PPGAutoencoder(lz=115)
    model.train()
    inp = torch.randn(4, 1, 1000, requires_grad=True)
    x_hat_norm, z, scale_a = model(inp, use_ste=True)

    mus = torch.zeros(4, 1, 1)
    x_hat_raw = x_hat_norm * stds.view(4, 1, 1) + mus
    x_raw = inp * stds.view(4, 1, 1) + mus
    x_raw_seq = x_raw.view(1, 4000)
    x_hat_raw_seq = x_hat_raw.view(1, 4000)

    loss_g, _ = loss_fn(x_raw, x_hat_raw, x_raw_seq, x_hat_raw_seq, block_scales=stds)
    loss_g.backward()

    # Xác nhận gradient tồn tại, hữu hạn, khác 0 trên tất cả các tầng
    for name, p in model.named_parameters():
        assert p.grad is not None, f"Gradient cua {name} bi thieu"
        assert torch.all(torch.isfinite(p.grad)), f"Gradient cua {name} chua NaN/Inf"
        assert torch.any(p.grad != 0), f"Gradient cua {name} hoan toan bang 0"


# =============================================================================
# CỔNG 3: LƯỢNG TỬ ĐỐI XỨNG [-32767, 32767], EPSILON 1e-8 VÀ PACKET BITSTREAM
# =============================================================================
def test_gate3_quantization_edge_cases_and_byte_equivalence():
    # 1. Vector toàn 0: a = 1e-8, q = 0
    z_zero = np.zeros(115, dtype=np.float32)
    q_z, a_z = quantize_latent(z_zero)
    assert abs(a_z - 1e-8) < 1e-12
    assert np.all(q_z == 0)

    # 2. Vector cực nhỏ dưới 1e-8: a được kẹp ở 1e-8
    z_subnormal = np.array([1e-12, -2e-11, 5e-10], dtype=np.float32)
    q_sub, a_sub = quantize_latent(z_subnormal)
    assert abs(a_sub - 1e-8) < 1e-12
    assert np.all(q_sub == 0)

    # 3. Vector cực đại đối xứng: giới hạn đúng trong [-32767, 32767]
    z_extreme = np.array([1000.0, -1000.0, 500.0, -500.0], dtype=np.float32)
    q_ext, a_ext = quantize_latent(z_extreme)
    assert abs(a_ext - (1000.0 / 32767.0)) < 1e-8
    assert q_ext[0] == 32767
    assert q_ext[1] == -32767  # Đối xứng chặt, không phải -32768
    assert int(np.min(q_ext)) >= -32767
    assert int(np.max(q_ext)) <= 32767

    # 4. Bắt lỗi số không hữu hạn
    with pytest.raises(ValueError):
        quantize_latent(np.array([1.0, np.nan, 2.0], dtype=np.float32))
    with pytest.raises(ValueError):
        quantize_latent(np.array([1.0, np.inf, 2.0], dtype=np.float32))

    # 5. Đối chiếu mô phỏng STE với byte nhị phân thực
    torch.manual_seed(2026)
    model = PPGAutoencoder(lz=115).eval()
    x_test = torch.randn(1, 1, 1000)
    with torch.no_grad():
        z_t, scale_t = model.encoder(x_test)
        z_ste = simulate_quantization(z_t, scale_t).cpu().numpy().ravel()

    z_raw = z_t.cpu().numpy().ravel()
    q_act, a_act = quantize_latent(z_raw)
    pkt = pack_payload(version=1, config_id=1, sequence=10, mu=0.5, s=1.2, a=a_act, payload_int16=q_act)
    unp = unpack_payload(pkt, expected_lz=115, expected_config_id=1)
    assert unp["valid"] and unp["crc_valid"]
    z_bytes = dequantize_latent(unp["payload_int16"], unp["a"])

    assert np.allclose(z_ste, z_bytes, atol=1e-6), "Mo phong STE va duong packet khong khop nhau"


# =============================================================================
# CỔNG 4: BỘ ĐỌC HR PHÂN BIỆT RÕ TRUNG VỊ VỚI TRUNG BÌNH KHOẢNG NHỊP
# =============================================================================
def test_gate4_hr_irregular_intervals_distinguishes_mean_from_median():
    fs = 125
    # Tạo tín hiệu PPG với 6 đỉnh:
    # 5 khoảng nhịp đều = 0.8 giây (tương ứng 75 bpm)
    # 1 khoảng nhịp dị vị ngoại lai = 2.0 giây (do ngoại tâm thu / nhiễu)
    # Trung bình khoảng nhịp = (5 * 0.8 + 2.0) / 6 = 1.0 giây -> Nếu dùng trung bình sẽ ra 60 bpm!
    # Trung vị khoảng nhịp = 0.8 giây -> Bộ đọc chuẩn đề cương phải ra 75 bpm!
    peak_times = [0.0, 0.8, 1.6, 2.4, 3.2, 4.0, 6.0]
    total_samples = int(7.0 * fs)
    sig = np.zeros(total_samples, dtype=np.float32)

    for pt in peak_times:
        idx = int(pt * fs)
        if 0 <= idx < total_samples:
            sig[idx] = 2.0
            if idx > 0: sig[idx - 1] = 1.0
            if idx < total_samples - 1: sig[idx + 1] = 1.0

    hr_est = estimate_hr_peaks(sig, fs=fs, min_peaks=3, min_bpm=30.0, max_bpm=210.0, prominence_factor=0.2)
    assert not np.isnan(hr_est)
    assert abs(hr_est - 75.0) < 1.0, f"HR est phai la 75 bpm (trung vi), thuc te {hr_est} (neu ra 60 bpm la loi trung binh)"

    # Ít hơn 3 đỉnh phải trả về NaN
    sig_few = np.zeros(total_samples, dtype=np.float32)
    sig_few[100] = 2.0
    sig_few[300] = 2.0
    assert np.isnan(estimate_hr_peaks(sig_few, fs=fs, min_peaks=3))


# =============================================================================
# CỔNG 5: BỘ ĐỌC RR - MẬT ĐỘ NHỊP, THIẾU ĐẶC TRƯNG VÀ ĐỒNG THUẬN RIIV/RIAV <= 3
# =============================================================================
def test_gate5_rr_synthetic_beat_density_and_consensus():
    fs = 125
    t = np.arange(0, 32, 1.0 / fs)

    # 1. Tín hiệu tổng hợp chuẩn: HR = 75 bpm, RR = 15 brpm
    cardiac = np.sin(2 * np.pi * (75.0 / 60.0) * t)
    resp = 1.0 + 0.3 * np.sin(2 * np.pi * (15.0 / 60.0) * t)
    baseline = 0.2 * np.sin(2 * np.pi * (15.0 / 60.0) * t)
    ppg = cardiac * resp + baseline

    rr_val, status = estimate_rr_riiv_riav(ppg, fs=fs, max_discrepancy=3.0, search_band=(0.1, 0.7))
    assert status["valid"]
    assert abs(rr_val - 15.0) < 1.5

    # 2. Tín hiệu mật độ nhịp quá thấp / thiếu đặc trưng
    t_slow = np.arange(0, 32, 1.0 / fs)
    ppg_slow = np.sin(2 * np.pi * (20.0 / 60.0) * t_slow)
    rr_slow, status_slow = estimate_rr_riiv_riav(ppg_slow, fs=fs, max_discrepancy=3.0, search_band=(0.1, 0.7))
    assert np.isnan(rr_slow)
    assert status_slow["failure_reason"] in ["insufficient_beat_density", "insufficient_features", "insufficient_paired_features"]

    # Kiểm tra kích hoạt chính xác insufficient_beat_density khi có khoảng gián đoạn nhịp lớn (gap >= 5s)
    sig_gap = np.zeros_like(t)
    for sec in [1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 18, 19, 20, 21, 22]:
        idx = int(sec * fs)
        sig_gap[idx] = 2.0
        sig_gap[idx + 1] = 1.0
        sig_gap[idx - 1] = 1.0
        idx_t = idx + int(0.4 * fs)
        sig_gap[idx_t] = -2.0
        sig_gap[idx_t + 1] = -1.0
        sig_gap[idx_t - 1] = -1.0
    _, st_gap = estimate_rr_riiv_riav(sig_gap, fs=fs, search_band=(0.1, 0.7))
    assert st_gap["failure_reason"] == "insufficient_beat_density"

    # 3. Tín hiệu phẳng / thiếu đặc trưng (< 8 đỉnh)
    ppg_flat = np.ones(4000, dtype=np.float32) * 0.5
    rr_flat, status_flat = estimate_rr_riiv_riav(ppg_flat, fs=fs)
    assert np.isnan(rr_flat)
    assert status_flat["failure_reason"] in ["flat_or_nonfinite_signal", "insufficient_features"]

    # 4. Ngưỡng đồng thuận: phân biệt rõ ngưỡng nhãn (2.0) với thuật toán (3.0)
    from prepare_data import extract_rr_ref
    import pandas as pd
    # Giả lập 2 annotator chênh nhau 2.5 brpm:
    # - Với extract_rr_ref (max_diff=2.0): KHÔNG hợp lệ
    # - Với estimate_rr_riiv_riav (max_discrepancy=3.0): hợp lệ
    marks1 = np.arange(0, 32 * fs, int(fs * (60.0 / 15.0)))  # 15 brpm
    marks2 = np.arange(0, 32 * fs, int(fs * (60.0 / 17.5)))  # 17.5 brpm
    df_breaths = pd.DataFrame({
        "Annotator1": pd.Series(marks1),
        "Annotator2": pd.Series(marks2)
    })
    label_rr = extract_rr_ref(df_breaths, 0, 32, fs=fs, max_diff=2.0)
    assert np.isnan(label_rr), "Chenh lech 2.5 brpm phai bi tu choi boi nguong nhan 2.0"
    label_rr_relaxed = extract_rr_ref(df_breaths, 0, 32, fs=fs, max_diff=3.0)
    assert not np.isnan(label_rr_relaxed), "Chenh lech 2.5 brpm duoc chap nhan khi nguong la 3.0"


# =============================================================================
# CỔNG 6: CRC CHUẨN 123456789 -> 0x29B1, GÓI SAI VÀ SEQUENCE TRACKER
# =============================================================================
def test_gate6_crc_errors_and_sequence_tracking():
    # 1. CRC chuẩn ASCII '123456789' cho ra đúng 0x29B1
    assert compute_crc16(b"123456789") == 0x29B1

    # 2. Tạo gói chuẩn 8x (250 byte)
    z = np.zeros(115, dtype=np.float32)
    q, a = quantize_latent(z)
    pkt_8x = pack_payload(version=1, config_id=1, sequence=100, mu=0.5, s=1.2, a=a, payload_int16=q)
    assert len(pkt_8x) == 250

    # 3. Gói sai phiên bản / cấu hình / độ dài / CRC
    assert not unpack_payload(pkt_8x, expected_config_id=2)["valid"]
    assert not unpack_payload(pkt_8x[:-2], expected_lz=115)["valid"]
    
    # Gói version 99 có CRC hoàn toàn hợp lệ nhưng vẫn phải bị từ chối
    import struct
    bad_v99_bytes = bytearray(pkt_8x)
    bad_v99_bytes[0] = 99
    crc_v99 = compute_crc16(bytes(bad_v99_bytes[:-2]))
    bad_v99_bytes[-2:] = struct.pack('<H', crc_v99)
    unp_v99 = unpack_payload(bytes(bad_v99_bytes), expected_version=1)
    assert not unp_v99["valid"], "Gói phiên bản 99 phải bị từ chối dù CRC-16 đúng"
    
    bad_pkt = bytearray(pkt_8x)
    bad_pkt[15] ^= 0x01
    assert not unpack_payload(bytes(bad_pkt), expected_lz=115)["valid"]

    # 4. Mất gói, trùng lặp và đảo thứ tự
    tracker = PacketSequenceTracker()
    unp100 = unpack_payload(pkt_8x, expected_lz=115, expected_config_id=1)
    assert tracker.feed_packet(unp100)
    
    # Trùng lặp seq 100
    assert not tracker.feed_packet(unp100)
    assert tracker.duplicate_count == 1

    # Đảo thứ tự: seq 98 < 100
    unp98 = dict(unp100)
    unp98["sequence"] = 98
    assert not tracker.feed_packet(unp98)
    assert tracker.out_of_order_count == 1

    # Mất gói: nhảy từ 100 lên 103
    pkt103 = pack_payload(version=1, config_id=1, sequence=103, mu=0.5, s=1.2, a=a, payload_int16=q)
    unp103 = unpack_payload(pkt103, expected_lz=115, expected_config_id=1)
    assert not tracker.feed_packet(unp103)
    assert tracker.missing_count == 2

    # 5. Ghép 4 gói liên tiếp thành ngữ cảnh 32 giây:
    # 4 gói cùng cấu hình và liên tiếp -> Hợp lệ
    pkts_valid = [
        unpack_payload(pack_payload(version=1, config_id=1, sequence=s, mu=0.5, s=1.2, a=a, payload_int16=q))
        for s in [200, 201, 202, 203]
    ]
    assert PacketSequenceTracker.can_form_context(pkts_valid, expected_config_id=1)
    
    # 4 gói liên tiếp nhưng khác cấu hình (ví dụ gói thứ 3 là cấu hình 2) -> BẮT BUỘC BỊ TỪ CHỐI
    pkts_mixed_config = [
        unpack_payload(pack_payload(version=1, config_id=1, sequence=200, mu=0.5, s=1.2, a=a, payload_int16=q)),
        unpack_payload(pack_payload(version=1, config_id=1, sequence=201, mu=0.5, s=1.2, a=a, payload_int16=q)),
        unpack_payload(pack_payload(version=1, config_id=2, sequence=202, mu=0.5, s=1.2, a=a, payload_int16=np.zeros(52, dtype=np.int16))),
        unpack_payload(pack_payload(version=1, config_id=1, sequence=203, mu=0.5, s=1.2, a=a, payload_int16=q)),
    ]
    assert not PacketSequenceTracker.can_form_context(pkts_mixed_config, expected_config_id=1), "Không được ghép ngữ cảnh từ các gói khác cấu hình"


# =============================================================================
# CỔNG 7: LỌC THEO KHỐI SOS, SỐ THỰC 32-BIT, CHIA BỆNH NHÂN VÀ TỔNG HỢP THỐNG KÊ
# =============================================================================
def test_gate7_filtering_float32_splits_and_statistics():
    # 1. Lọc theo khối giữ trạng thái tương đương lọc toàn chuỗi
    fs = 125
    sos = butter(N=2, Wn=[0.05, 8.0], btype='bandpass', fs=fs, output='sos')
    rng = np.random.RandomState(2026)
    sig_raw = rng.randn(4000).astype(np.float64)

    filt_continuous = sosfilt(sos, sig_raw)

    zi = np.zeros((sos.shape[0], 2), dtype=np.float64)
    chunks = []
    for b in range(4):
        chunk_filt, zi = sosfilt(sos, sig_raw[b * 1000:(b + 1) * 1000], zi=zi)
        chunks.append(chunk_filt)
    filt_blocked = np.concatenate(chunks)
    assert np.allclose(filt_continuous, filt_blocked, atol=1e-6)

    # 2. Chênh lệch khi chuyển sang số thực 32-bit là cực nhỏ (dưới 5e-4 trên toàn chuỗi 4000 mẫu)
    sig_f32 = sig_raw.astype(np.float32)
    sos_f32 = sos.astype(np.float32)
    filt_f32 = sosfilt(sos_f32, sig_f32)
    max_f32_diff = float(np.max(np.abs(filt_continuous - filt_f32)))
    assert max_f32_diff < 5e-4
    assert np.allclose(filt_continuous, filt_f32, atol=5e-4)

    # 3. Phân chia đối tượng nguồn MIMIC II độc lập (Subject-wise, không theo số bản ghi)
    splits_path = os.path.join(BASE_DIR, "data", "splits_subject.json")
    with open(splits_path, "r", encoding="utf-8") as f:
        splits = json.load(f)
        
    test_subs = set(splits["test_subjects"])
    dev_subs = set(splits["dev_subjects"])
    test_recs = set(splits["test_records"])
    dev_recs = set(splits["dev_records"])
    
    assert len(test_subs) == 10, f"Kỳ vọng 10 test subjects, nhận {len(test_subs)}"
    assert len(test_recs) == 13, f"Kỳ vọng 13 test records, nhận {len(test_recs)}"
    assert len(dev_subs) == 36, f"Kỳ vọng 36 dev subjects, nhận {len(dev_subs)}"
    assert len(dev_recs) == 40, f"Kỳ vọng 40 dev records, nhận {len(dev_recs)}"
    
    # Không giao nhau giữa dev và test trên cả mã đối tượng nguồn và số bản ghi
    assert len(test_subs.intersection(dev_subs)) == 0, "Rò rỉ đối tượng nguồn MIMIC giữa dev và test!"
    assert len(test_recs.intersection(dev_recs)) == 0, "Rò rỉ bản ghi giữa dev và test!"
    
    # Kiểm tra đặc biệt: nhóm s11342 (bản ghi 20, 21, 22, 23) phải nằm TRỌN VẸN ở tập kiểm thử
    assert set([20, 21, 22, 23]).issubset(test_recs), "s11342 không nằm trọn vẹn trong test set!"
    # Các nhóm nhiều bản ghi s03386 (6, 7, 8, 9) và s25323 (38, 39) phải nằm TRỌN VẸN ở tập phát triển
    assert set([6, 7, 8, 9]).issubset(dev_recs), "s03386 không nằm trọn vẹn trong dev set!"
    assert set([38, 39]).issubset(dev_recs), "s25323 không nằm trọn vẹn trong dev set!"
    
    # Kiểm tra 5 nếp kiểm định: không có nếp nào chia cắt đối tượng nguồn sang hai phía
    for f_name, f_info in splits["folds"].items():
        tr_s = set(f_info["train_subjects"])
        val_s = set(f_info["val_subjects"])
        assert len(tr_s.intersection(val_s)) == 0, f"Rò rỉ đối tượng nguồn MIMIC tại {f_name}"
        # s03386 và s25323 không bị tách hai phía
        for ms in ["s03386", "s25323"]:
            assert not (ms in tr_s and ms in val_s), f"Đối tượng {ms} bị phân tán qua train và val tại {f_name}"

    # 4. Bootstrap người bệnh và tính Dr không nhầm với Delta-MAE
    # Giả lập 10 đối tượng có MAE uncompressed và MAE reconstructed
    uncomp_p = {f"s_{i}": 4.0 for i in range(10)}
    rec_p = {f"s_{i}": 4.5 for i in range(10)}
    delta_p = {f"s_{i}": (rec_p[f"s_{i}"] - uncomp_p[f"s_{i}"]) for i in range(10)}
    d_mean, d_low, d_high = bootstrap_paired_diff_ci(delta_p, n_boot=2000, seed=2026)
    assert abs(d_mean - 0.5) < 1e-4
    assert abs(d_low - 0.5) < 1e-4 and abs(d_high - 0.5) < 1e-4


# =============================================================================
# KIỂM THỬ HỆ THỐNG VÀ REGRESSION TESTS
# =============================================================================
def test_perfect_reconstruction_has_infinite_snrc():
    signal = np.arange(1000, dtype=np.float64)
    prd, prdc, snrc = calc_prd_prdc_snrc(signal, signal.copy())
    assert prd == 0.0 and prdc == 0.0
    assert np.isposinf(snrc)


def test_parameter_counts_exact():
    model_8x = PPGAutoencoder(lz=115)
    tot_params = sum(p.numel() for p in model_8x.parameters())
    enc_params = sum(p.numel() for p in model_8x.encoder.parameters())
    dec_params = sum(p.numel() for p in model_8x.decoder.parameters())
    assert tot_params == 65419
    assert enc_params == 32642
    assert dec_params == 32777


def test_yaml_config_matches_outline():
    import yaml
    cfg_path = os.path.join(BASE_DIR, "configs", "c5.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert cfg["training"]["max_epochs"] == 150
    assert cfg["training"]["lr_patience"] == 5
    assert cfg["training"]["early_stop_patience"] == 15
    assert cfg["training"]["loss"]["beta"] == 0.5
    assert cfg["training"]["loss"]["hr_band"] == [0.8, 3.0]
    assert cfg["training"]["loss"]["hr_weight"] == 2.0
    assert cfg["training"]["loss"]["rr_band"] == [0.1, 0.4]
    assert cfg["training"]["loss"]["rr_weight"] == 4.0
    assert cfg["training"]["loss"]["eps_f"] == 1.0e-8
    assert cfg["evaluation"]["rr_estimator"]["search_band"] == [0.1, 0.7]
    assert cfg["evaluation"]["rr_estimator"]["max_discrepancy"] == 3.0
    assert cfg["data"]["label_rules"]["rr_max_consensus_diff"] == 2.0
    assert cfg["evaluation"]["dev_subjects_count"] == 36
    assert cfg["evaluation"]["test_subjects_count"] == 10
    assert cfg["evaluation"]["dev_records_count"] == 40
    assert cfg["evaluation"]["test_records_count"] == 13


def test_rr_label_rules_minimum_three_marks():
    from prepare_data import extract_rr_ref
    import pandas as pd
    
    # 1. Trường hợp chỉ có 2 mốc thở -> BẮT BUỘC BỊ LOẠI (NaN)
    # 2 mốc ở mẫu 100 và 350 (fs=125 -> 2s, 1 chu kỳ)
    df_2marks = pd.DataFrame({
        "annotator1": [100, 350, np.nan, np.nan],
        "annotator2": [105, 355, np.nan, np.nan]
    })
    rr_2m = extract_rr_ref(df_2marks, t_start=0.0, t_end=32.0, fs=125, max_diff=2.0)
    assert np.isnan(rr_2m), "Chỉ có 2 mốc thở phải trả về NaN theo yêu cầu tối thiểu 3 mốc"

    # 2. Trường hợp có ít nhất 3 mốc thở và đồng thuận -> HỢP LỆ
    # 3 mốc: 100, 475, 850 (khoảng cách 375 mẫu = 3s -> 20 nhịp thở/phút)
    df_3marks = pd.DataFrame({
        "annotator1": [100, 475, 850, np.nan],
        "annotator2": [105, 480, 855, np.nan]
    })
    rr_3m = extract_rr_ref(df_3marks, t_start=0.0, t_end=32.0, fs=125, max_diff=2.0)
    assert not np.isnan(rr_3m), "Có 3 mốc thở đồng thuận phải trích xuất được nhãn"
    assert abs(rr_3m - 20.0) < 0.5

    # 3. Trường hợp 2 người chú thích lệch > 2.0 brpm -> BỊ LOẠI DO BẤT ĐỒNG THUẬN
    df_disagree = pd.DataFrame({
        "annotator1": [100, 475, 850, np.nan], # ~20 brpm
        "annotator2": [100, 350, 600, 850]     # ~24 brpm (lệch > 2.0)
    })
    rr_dis = extract_rr_ref(df_disagree, t_start=0.0, t_end=32.0, fs=125, max_diff=2.0)
    assert np.isnan(rr_dis), "Lệch chú thích > 2.0 brpm phải trả về NaN"


def test_loss_requires_block_scales_and_nonempty():
    loss_fn = MultiScaleSpectralLoss(fs=125, beta=0.5)
    
    x = torch.randn(4, 1, 1000)
    x_hat = torch.randn(4, 1, 1000)
    x_32 = torch.randn(1, 4000)
    x_hat_32 = torch.randn(1, 4000)
    
    # 1. Thiếu block_scales -> Phải ném ValueError theo Công thức (6)
    with pytest.raises(ValueError, match="block_scales"):
        loss_fn(x, x_hat, x_32, x_hat_32, block_scales=None)
        
    # 2. Batch rỗng -> Phải ném ValueError
    empty_x = torch.empty(0, 1, 1000)
    with pytest.raises(ValueError, match="rỗng"):
        loss_fn(empty_x, empty_x, x_32, x_hat_32, block_scales=torch.ones(0, 1))

