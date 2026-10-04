import os
import sys
import time
import platform
import tracemalloc
import socket
import numpy as np
import torch
from scipy.signal import sosfilt
from utils.deployment_reference import configured_sos, load_raw_benchmark_blocks

from utils.packet_codec import (
    pack_payload, unpack_payload, quantize_latent, run_bit_flip_experiment
)

def get_system_environment_info() -> dict:
    import psutil
    info = {
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "pytorch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda if torch.cuda.is_available() else None,
        "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU Host",
        "cpu_arch": platform.processor() or platform.machine(),
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "torch_intraop_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
        "benchmark_device": "CPU",
        "model_format": "TorchScript",
        "weight_dtype": "FLOAT32",
        "payload_dtype": "INT16",
        "raw_signal_dtype": "FLOAT32",
        "sos_filter_and_state_dtype": "FLOAT64",
        "filtered_signal_and_normalization_dtype": "FLOAT64 before explicit FLOAT32 encoder input",
        "latency_memory_separation": "Timing with tracemalloc disabled; Python allocation profile in separate pass"
    }
    return info

def _benchmark_single_encoder(encoder_model_path: str, real_blocks: list, lz: int = 115,
                              config_id: int = 1, label: str = "8x",
                              n_warmup: int = 20, n_runs: int = 100,
                              seed: int = 2026, proc=None, config=None) -> dict:
    print(f"\n==================================================================")
    print(f"BENCHMARK THAM CHIEU TREN CPU CHO CAU HINH {label} (Lz={lz})")
    print(f"==================================================================")
    
    device = torch.device('cpu')
    encoder = torch.jit.load(encoder_model_path, map_location=device)
    encoder.eval()
    
    # 1. Kich thuoc tep mo hinh va trong so
    model_size_bytes = os.path.getsize(encoder_model_path)
    model_size_kb = model_size_bytes / 1024.0
    
    # Tinh dung luong trong so FLOAT32
    num_params = sum(p.numel() for p in encoder.parameters())
    weight_size_kb = (num_params * 4) / 1024.0
    print(f"1. Kich thuoc tep mo hinh TorchScript: {model_size_kb:.2f} KB | Trong so ({num_params:,} tham so): {weight_size_kb:.2f} KB")
    
    # Khoi tao bo loc Butterworth SOS cho do dac tien xu ly nhan qua
    if config is None:
        raise ValueError("CPU benchmark requires the locked configuration")
    sos = configured_sos(config)
    
    if len(real_blocks) < n_warmup + n_runs:
        raise ValueError("Insufficient chronological raw blocks for warmup and measurement")
    
    # 2. Khoi dong warm-up tren n_warmup khoi thuc
    print(f"2. Thuc hien {n_warmup} luot warm-up tren du lieu thuc te...")
    for idx in range(n_warmup):
        block = real_blocks[idx]
        raw_b = block["signal"]
        filtered, zi = sosfilt(sos, raw_b, zi=block["filter_state"].copy())
        mu = float(np.mean(filtered))
        s = max(float(np.std(filtered)), float(config["data"]["normalization"]["eps"]))
        x_norm = (filtered - mu) / s
        inp = torch.tensor(x_norm.reshape(1, 1, 1000), dtype=torch.float32)
        with torch.no_grad():
            z, scale_a = encoder(inp)
        q, a = quantize_latent(z.numpy().squeeze())
        _ = pack_payload(1, config_id, idx, mu, s, a, q)
        
    # 3. Do dac thoi gian chi tiet tren n_runs luot chay
    print(f"3. Do dac thoi gian thuc thi ({n_runs} luot chay tren mau PPG thuc te)...")
    t_prep_list = []
    t_norm_list = []
    t_enc_list = []
    t_quant_list = []
    t_pack_list = []
    t_tx_list = []
    t_compute_list = []
    t_total_with_tx_list = []

    rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx_socket.bind(("127.0.0.1", 0))
    rx_socket.settimeout(2.0)
    tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx_address = rx_socket.getsockname()
    
    # Đảm bảo tracemalloc tắt hoàn toàn trong lúc đo thời gian để tránh phụ phí can thiệp của bộ nhớ Python
    if tracemalloc.is_tracing():
        tracemalloc.stop()

    last_packet = None
    for run_idx in range(n_runs):
        block = real_blocks[n_warmup + run_idx]
        raw_b = block["signal"]
        initial_state = block["filter_state"].copy()
        
        # Mốc 0: Bắt đầu tiền xử lý
        t0 = time.perf_counter()
        
        # Công đoạn 1: Tiền xử lý nhân quả (Lọc Butterworth SOS)
        filtered, zi = sosfilt(sos, raw_b, zi=initial_state)
        t1 = time.perf_counter()
        
        # Công đoạn 2: Chuẩn hóa Z-score
        mu = float(np.mean(filtered))
        s = max(float(np.std(filtered)), float(config["data"]["normalization"]["eps"]))
        x_norm = (filtered - mu) / s
        inp = torch.tensor(x_norm.reshape(1, 1, 1000), dtype=torch.float32)
        t2 = time.perf_counter()
        
        # Công đoạn 3: Suy luận tích chập 1D-CNN Encoder
        with torch.no_grad():
            z, scale_a = encoder(inp)
        z_np = z.numpy().squeeze()
        t3 = time.perf_counter()
        
        # Công đoạn 4: Lượng tử hóa số nguyên 16-bit
        q, a = quantize_latent(z_np)
        t4 = time.perf_counter()
        
        # Công đoạn 5: Đóng gói khung nhị phân và tính CRC-16
        packet = pack_payload(1, config_id, run_idx, mu, s, a, q)
        t5 = time.perf_counter()

        # Công đoạn 6: Truyền UDP cục bộ
        tx_socket.sendto(packet, rx_address)
        received, _ = rx_socket.recvfrom(4096)
        if received != packet:
            raise RuntimeError("Dữ liệu UDP loopback không toàn vẹn")
        t6 = time.perf_counter()
        
        last_packet = packet
        
        t_prep_list.append((t1 - t0) * 1000.0)
        t_norm_list.append((t2 - t1) * 1000.0)
        t_enc_list.append((t3 - t2) * 1000.0)
        t_quant_list.append((t4 - t3) * 1000.0)
        t_pack_list.append((t5 - t4) * 1000.0)
        t_tx_list.append((t6 - t5) * 1000.0)
        t_compute_list.append((t5 - t0) * 1000.0)
        t_total_with_tx_list.append((t6 - t0) * 1000.0)

    tx_socket.close()
    rx_socket.close()
        
    # Lượt đo bộ nhớ riêng biệt (tracemalloc ON) để không làm méo số đo thời gian
    tracemalloc.start()
    rss_before_mb = proc.memory_info().rss / (1024.0 * 1024.0) if proc else 0.0
    for m_idx in range(min(10, len(real_blocks))):
        blk = real_blocks[m_idx]
        f_sig, _ = sosfilt(sos, blk["signal"], zi=blk["filter_state"].copy())
        mu_m = float(np.mean(f_sig))
        s_m = max(float(np.std(f_sig)), float(config["data"]["normalization"]["eps"]))
        inp_m = torch.tensor(((f_sig - mu_m) / s_m).reshape(1, 1, 1000), dtype=torch.float32)
        with torch.no_grad():
            z_m, _ = encoder(inp_m)
        q_m, a_m = quantize_latent(z_m.numpy().squeeze())
        _ = pack_payload(1, config_id, m_idx, mu_m, s_m, a_m, q_m)
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    
    python_tracemalloc_peak_mb = peak_mem / (1024.0 * 1024.0)
    host_rss_mb = proc.memory_info().rss / (1024.0 * 1024.0) if proc else 0.0
    host_rss_delta_mb = host_rss_mb - rss_before_mb
    
    prep_med, prep_p95 = float(np.median(t_prep_list)), float(np.percentile(t_prep_list, 95))
    norm_med, norm_p95 = float(np.median(t_norm_list)), float(np.percentile(t_norm_list, 95))
    enc_med, enc_p95 = float(np.median(t_enc_list)), float(np.percentile(t_enc_list, 95))
    quant_med, quant_p95 = float(np.median(t_quant_list)), float(np.percentile(t_quant_list, 95))
    pack_med, pack_p95 = float(np.median(t_pack_list)), float(np.percentile(t_pack_list, 95))
    tx_med, tx_p95 = float(np.median(t_tx_list)), float(np.percentile(t_tx_list, 95))
    total_med, total_p95 = float(np.median(t_compute_list)), float(np.percentile(t_compute_list, 95))
    total_tx_med = float(np.median(t_total_with_tx_list))
    total_tx_p95 = float(np.percentile(t_total_with_tx_list, 95))
    
    print(f"\n--- KET QUA THOI GIAN THUC THI [{label}] (ms / khoi 8 giay) ---")
    print(f" - Loc SOS nhan qua:     Trung vi = {prep_med:.3f} ms | P95 = {prep_p95:.3f} ms")
    print(f" - Chuan hoa Z-score:    Trung vi = {norm_med:.3f} ms | P95 = {norm_p95:.3f} ms")
    print(f" - Ma hoa 1D-CNN:        Trung vi = {enc_med:.3f} ms | P95 = {enc_p95:.3f} ms")
    print(f" - Luong tu hoa INT16:   Trung vi = {quant_med:.3f} ms | P95 = {quant_p95:.3f} ms")
    print(f" - Dong goi va CRC-16:   Trung vi = {pack_med:.3f} ms | P95 = {pack_p95:.3f} ms")
    print(f" - UDP loopback host:    Trung vi = {tx_med:.3f} ms | P95 = {tx_p95:.3f} ms")
    print(f" -> TONG TINH TOAN BIEN: Trung vi = {total_med:.3f} ms | P95 = {total_p95:.3f} ms")
    
    return {
        "warmup_runs": n_warmup,
        "measurement_runs": n_runs,
        "input_provenance": "Unfiltered WFDB PLETH, FLOAT32, chronological nonoverlapping blocks",
        "filter_initialization": "Zero state per finite patient segment; configured signal warmup discarded",
        "memory_scope": "RSS before/after are process snapshots, not peaks; tracemalloc covers Python allocations only",
        "measured_blocks": [{"pid": b["pid"], "start_sample": b["start_sample"]} for b in real_blocks[n_warmup:n_warmup+n_runs]],
        "timings_ms": {"preprocessing": t_prep_list, "normalization": t_norm_list,
                       "encoding": t_enc_list, "quantization": t_quant_list,
                       "packing": t_pack_list, "udp_local": t_tx_list,
                       "compute_total": t_compute_list, "total_with_udp": t_total_with_tx_list},
        "label": label,
        "lz": lz,
        "config_id": config_id,
        "num_params": num_params,
        "weight_size_kb": float(weight_size_kb),
        "model_size_kb": float(model_size_kb),
        "prep_median_ms": prep_med,
        "prep_p95_ms": prep_p95,
        "norm_median_ms": norm_med,
        "norm_p95_ms": norm_p95,
        "enc_median_ms": enc_med,
        "enc_p95_ms": enc_p95,
        "quant_median_ms": quant_med,
        "quant_p95_ms": quant_p95,
        "pack_median_ms": pack_med,
        "pack_p95_ms": pack_p95,
        "host_udp_loopback_median_ms": tx_med,
        "host_udp_loopback_p95_ms": tx_p95,
        "total_median_ms": total_med,
        "total_p95_ms": total_p95,
        "total_with_host_udp_median_ms": total_tx_med,
        "total_with_host_udp_p95_ms": total_tx_p95,
        "input_buffer_bytes": 1000 * 4,
        "latent_buffer_bytes": int(lz * 4),
        "packet_buffer_bytes": int(20 + 2 * lz),
        "sos_state_buffer_bytes": int(2 * 2 * 8),  # 2 sections * 2 states * 8 bytes (double)
        "python_tracemalloc_peak_mb": float(python_tracemalloc_peak_mb),
        "host_process_rss_mb": float(host_rss_mb),
        "host_process_rss_before_mb": float(rss_before_mb),
        "host_process_rss_delta_mb": float(host_rss_delta_mb),
        "last_packet": last_packet
    }

def run_edge_benchmark(encoder_model_path: str = None, real_blocks: list = None, lz: int = 115, 
                       n_warmup: int = 20, n_runs: int = 100, seed: int = 2026,
                       encoder_path_8x: str = None, encoder_path_16x: str = None,
                       config: dict = None, raw_sources: list = None) -> dict:
    print("==================================================================")
    print("BAT DAU BENCHMARK THAM CHIEU TREN CPU MAY TINH (DU LIEU THUC TE)")
    print("==================================================================")
    
    path_8x = encoder_path_8x or encoder_model_path
    path_16x = encoder_path_16x
    if path_16x is None and path_8x:
        # Tu dong tim path 16x neu cung thu muc
        cand = path_8x.replace("8x", "16x")
        if os.path.exists(cand) and cand != path_8x:
            path_16x = cand

    env_info = get_system_environment_info()
    print(f"He dieu hanh: {env_info['os']}")
    print(f"CPU: {env_info['cpu_arch']} ({env_info['physical_cpu_count']} core vat ly / {env_info['logical_cpu_count']} luong)")
    print(f"Phien ban Python: {env_info['python_version']}, PyTorch: {env_info['pytorch_version']}")
    
    import psutil
    proc = psutil.Process()
    rss_before_mb = proc.memory_info().rss / (1024.0 * 1024.0)

    # Do cau hinh 8x
    bench_8x = _benchmark_single_encoder(
        path_8x, real_blocks, lz=115, config_id=1, label="8x",
        n_warmup=n_warmup, n_runs=n_runs, seed=seed, proc=proc, config=config
    )
    
    # Do cau hinh 16x neu co
    bench_16x = None
    if path_16x and os.path.exists(path_16x):
        bench_16x = _benchmark_single_encoder(
            path_16x, real_blocks, lz=52, config_id=2, label="16x",
            n_warmup=n_warmup, n_runs=n_runs, seed=seed, proc=proc, config=config
        )
    
    # Kiem thu toan ven CRC-16 (10,000 phep thu lat bit ngau nhien tren goi 8x)
    print("\n4. Kiem thu toan ven CRC-16 (10,000 phep thu lat bit ngau nhien)...")
    flip_res = run_bit_flip_experiment(bench_8x["last_packet"], n_trials=10000, seed=seed)
    print(f" - Tong so phep thu: {flip_res['trials']}")
    print(f" - So lan phat hien: {flip_res['detected']}")
    print(f" - Ty le phat hien loi bit: {flip_res['detection_rate_pct']:.2f}%")
    assert flip_res['detected'] == 10000, "Loi: CRC khong phat hien het loi bit!"
    
    bench_8x["last_packet_hex"] = bench_8x.pop("last_packet", b"").hex()
    if bench_16x and "last_packet" in bench_16x:
        bench_16x["last_packet_hex"] = bench_16x.pop("last_packet", b"").hex()
    
    caveat_text = (
        "Lưu ý: Kết quả benchmark tham chiếu trên CPU máy tính (Intel/AMD), chưa phải kết quả "
        "triển khai và đo đạc trên vi điều khiển thực tế (như nRF52840 hoặc STM32WB55). "
        "Phép đo này không phản ánh điện năng tiêu thụ, thời gian ngủ sâu, thời lượng pin "
        "hay tài nguyên RAM vi điều khiển."
    )
    print("\n[Ghi chu khoa hoc]: Ket qua benchmark tham chieu tren CPU may tinh, chua phai vi dieu khien nhung.")
    
    benchmark_dict = {
        "raw_sources": raw_sources,
        "environment": env_info,
        "is_hardware_mcu": False,
        "benchmark_hardware": "CPU Host Reference (Chua phai vi dieu khien nhung)",
        "disclaimer": caveat_text,
        "bench_8x": bench_8x,
        "bench_16x": bench_16x,
        # Giu cac truong goc tu 8x cho tinh tuong thich nguoc
        "model_size_kb": bench_8x["model_size_kb"],
        "weight_size_kb": bench_8x["weight_size_kb"],
        "host_process_rss_mb": bench_8x["host_process_rss_mb"],
        "host_process_rss_before_mb": float(rss_before_mb),
        "host_process_rss_delta_mb": bench_8x["host_process_rss_delta_mb"],
        "python_tracemalloc_peak_mb": bench_8x["python_tracemalloc_peak_mb"],
        "peak_ram_mb": None,
        "prep_median_ms": bench_8x["prep_median_ms"],
        "prep_p95_ms": bench_8x["prep_p95_ms"],
        "norm_median_ms": bench_8x["norm_median_ms"],
        "norm_p95_ms": bench_8x["norm_p95_ms"],
        "enc_median_ms": bench_8x["enc_median_ms"],
        "enc_p95_ms": bench_8x["enc_p95_ms"],
        "quant_median_ms": bench_8x["quant_median_ms"],
        "quant_p95_ms": bench_8x["quant_p95_ms"],
        "pack_median_ms": bench_8x["pack_median_ms"],
        "pack_p95_ms": bench_8x["pack_p95_ms"],
        "host_udp_loopback_median_ms": bench_8x["host_udp_loopback_median_ms"],
        "host_udp_loopback_p95_ms": bench_8x["host_udp_loopback_p95_ms"],
        "total_median_ms": bench_8x["total_median_ms"],
        "total_p95_ms": bench_8x["total_p95_ms"],
        "total_with_host_udp_median_ms": bench_8x["total_with_host_udp_median_ms"],
        "total_with_host_udp_p95_ms": bench_8x["total_with_host_udp_p95_ms"],
        "input_buffer_bytes": bench_8x["input_buffer_bytes"],
        "latent_buffer_bytes": bench_8x["latent_buffer_bytes"],
        "packet_buffer_bytes": bench_8x["packet_buffer_bytes"],
        "sos_state_buffer_bytes": bench_8x["sos_state_buffer_bytes"],
        "stack_measurement": "Chưa đo (Không tách được stack riêng của encoder trong Python host; chưa có số liệu vi điều khiển)",
        "temporary_workspace_measurement": "Chưa đo (Không tách riêng được vùng làm việc của TorchScript trên Python host; không suy diễn từ kích thước tensor)",
        "energy_measurement": "Chưa đo (Không đo được trên máy tính; không suy ra thời lượng pin từ kích thước gói)",
        "mcu_board": None,
        "bit_flip_test": flip_res
    }
    return benchmark_dict

if __name__ == "__main__":
    from export_encoder import export_encoder_model
    out_pt_8x = "checkpoints/ppg_encoder_8x_traced.pt"
    out_pt_16x = "checkpoints/ppg_encoder_16x_traced.pt"
    if not os.path.exists(out_pt_8x):
        export_encoder_model(checkpoint_path="checkpoints/best_model_8x.pt", out_path=out_pt_8x, lz=115)
    if not os.path.exists(out_pt_16x):
        export_encoder_model(checkpoint_path="checkpoints/best_model_16x.pt", out_path=out_pt_16x, lz=52)
        
    import json
    import yaml
    with open("configs/c5.yaml", encoding="utf-8") as source:
        cfg = yaml.safe_load(source)
    with open(cfg["data"]["splits_file"], encoding="utf-8") as source:
        splits = json.load(source)
    blocks, sources = load_raw_benchmark_blocks(os.path.dirname(os.path.abspath(__file__)), cfg, splits["test_pids"])
    res = run_edge_benchmark(encoder_path_8x=out_pt_8x, encoder_path_16x=out_pt_16x,
                             real_blocks=blocks, config=cfg, raw_sources=sources)
