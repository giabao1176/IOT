"""Deterministic, unit-separated figures from the verified main evaluation."""
import json
from pathlib import Path
import numpy as np
import torch
import yaml
import matplotlib.pyplot as plt
from scipy.signal import welch
from models.autoencoder import PPGAutoencoder
from models.dct_baseline import DCTBaseline
from evaluate import reconstruct_block_with_model
from utils.artifact_integrity import validate_saved_checkpoint, validate_stage_fingerprint, sha256

BASE = Path(__file__).resolve().parent
FIG = BASE / "reports/figures"

def plot_samples():
    FIG.mkdir(parents=True, exist_ok=True)
    summary = json.loads((BASE / "results_summary.json").read_text(encoding="utf-8"))
    validate_stage_fingerprint(BASE, summary["stage_fingerprints"]["evaluation"])
    validate_stage_fingerprint(BASE, summary["stage_fingerprints"]["benchmark"])
    splits = json.loads((BASE / "data/splits_subject.json").read_text(encoding="utf-8"))
    records = np.load(BASE / "data/processed/bidmc_processed_dataset.npy", allow_pickle=True).tolist()
    # Exactly the chronological nonoverlap selection used by the main evaluation.
    selected = []
    for pid in sorted(splits["test_records"]):
        end = -1.
        for rec in sorted((r for r in records if r["pid"] == pid), key=lambda r: r["t_start"]):
            if rec["t_start"] >= end - 1e-4:
                selected.append(rec)
                end = rec["t_end"]
    rec = sorted(selected, key=lambda r: (r["subject_id"], r["pid"], r["t_start"]))[0]
    provenance = dict(subject_id=rec["subject_id"], record_id=rec["pid"], t_start=rec["t_start"],
                      selection="First main nonoverlapping context in subject/record/time order; not selected by error",
                      evaluation_fingerprint=summary["stage_fingerprints"]["evaluation"]["sha256"],
                      psd_input_samples=4000, welch_nperseg=4000, welch_nfft=4000,
                      frequency_spacing_hz=125/4000)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    methods = summary["evaluation"]["methods"]
    names = ["DCT-1D (8x)", "Autoencoder-MSE (8x)", "Boc tach B: Pho deu (8x)", "Boc tach C: Uu tien HR (8x)", "De xuat Day du (8x)"]
    labels = ["DCT", "MSE", "Bóc tách B", "Bóc tách C", "Đề xuất"]
    fig, axes = plt.subplots(1, 2, figsize=(8.5, 3.5), dpi=300)
    for ax, vals, unit, title in [
        (axes[0], [methods[n]["prdc_mean"] for n in names], "PRDc (%)", "Độ méo dạng sóng"),
        (axes[1], [methods[n]["intersection"]["mae_rr_mean"] for n in names], "MAE-RR (nhịp thở/phút)", "Sai số trên tập giao hợp lệ")]:
        bars = ax.bar(labels, vals, color="white", edgecolor="black", hatch="//")
        ax.bar_label(bars, fmt="%.2f", padding=3, fontsize=8)
        ax.set_ylim(0, max(vals)*1.25)
        ax.set_ylabel(unit)
        ax.set_title(title)
        ax.tick_params(axis="x", labelrotation=20)
    fig.tight_layout()
    fig.savefig(FIG / "fig1_comparison_8x.png")
    plt.close(fig)
    models = {}
    for level, lz in [("8x", 115), ("16x", 52)]:
        path = BASE / f"checkpoints/best_model_{level}.pt"
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        validate_saved_checkpoint(path, ckpt)
        model = PPGAutoencoder(lz).eval()
        model.load_state_dict(ckpt["model_state_dict"])
        models[level] = model
    signals = {"Gốc chưa nén": [], "DCT 8×": [], "Đề xuất 8×": [], "Đề xuất 16×": []}
    dct = DCTBaseline(lz=115)
    for i, b in enumerate(rec["blocks"]):
        signals["Gốc chưa nén"].append(b["signal_raw"])
        packet = dct.encode(b["signal_norm"], mu=b["mu"], s=b["s"])
        decoded, _ = dct.decode(packet, expected_config_id=1)
        if decoded is None:
            raise RuntimeError("DCT figure packet failed; never substitute original or zeros")
        signals["DCT 8×"].append(decoded.squeeze())
        for level, lz, cid in [("8x",115,1),("16x",52,2)]:
            restored, _ = reconstruct_block_with_model(models[level], b["signal_norm"], b["mu"], b["s"], lz, cid, i, "cpu")
            signals[f"Đề xuất {level.replace('x','×')}"].append(restored)
    signals = {k: np.concatenate(v) for k, v in signals.items()}
    styles = [("-",None),("--",None),("-", "s"),(":", "^")]
    fig, ax = plt.subplots(figsize=(8.5,3.5), dpi=300)
    for (label, signal), (style, marker) in zip(signals.items(), styles):
        ax.plot(np.arange(500)/125, signal[:500], color="black", linestyle=style,
                marker=marker, markevery=25, markersize=3, linewidth=1.2, label=label)
    ax.set(xlabel="Thời gian (giây)", ylabel="Biên độ (đơn vị tùy ý)",
           title=f"Dạng sóng tại bản ghi {rec['pid']}, từ giây {rec['t_start']:g}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "fig_waveform_reconstruction.png")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8.5,3.6), dpi=300)
    for (label, signal), (style, marker) in zip(signals.items(), styles):
        frequencies, power = welch(signal, fs=125, window="hann", nperseg=4000, noverlap=0, nfft=4000)
        ax.plot(frequencies, 10*np.log10(power+1e-12), color="black", linestyle=style,
                marker=marker, markevery=12, markersize=3, linewidth=1.2, label=label)
    ax.axvspan(.1,.4,facecolor="none",edgecolor="black",hatch="//",label="Dải ưu tiên RR của mất mát")
    ax.set(xlim=(0,3.5), xlabel="Tần số (Hz)", ylabel="Mật độ công suất (dB/Hz)",
           title="Phổ PPG của ngữ cảnh 32 giây, 4.000 mẫu")
    ax.legend(fontsize=7.5)
    fig.tight_layout()
    fig.savefig(FIG / "fig_spectrum_psd.png")
    plt.close(fig)
    # PC reference only, both compression levels, no MCU budget pass line.
    benchmark = summary["benchmark"]
    stages = ["Lọc", "Chuẩn hóa", "Mã hóa", "Lượng tử", "Đóng gói", "UDP cục bộ", "Tính toán"]
    keys = ["prep_median_ms","norm_median_ms","enc_median_ms","quant_median_ms","pack_median_ms","host_udp_loopback_median_ms","total_median_ms"]
    x = np.arange(len(stages))
    fig, ax = plt.subplots(figsize=(8.5,3.5), dpi=300)
    for shift, level, hatch in [(-.18,"8x","//"),(.18,"16x","xx")]:
        b = benchmark['bench_' + level]
        vals = [b[k] for k in keys]
        bars = ax.bar(x+shift, vals, .36, color="white", edgecolor="black", hatch=hatch, label=level)
        ax.bar_label(bars, fmt="%.2f", padding=2, fontsize=7)
    ax.set_xticks(x, stages)
    ax.set(ylabel="Trung vị thời gian (ms)", title="Đo tham chiếu CPU ở cả hai mức nén")
    ax.set_ylim(0, max(benchmark['bench_' + l]["total_median_ms"] for l in ["8x","16x"])*1.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG / "fig_latency_breakdown.png")
    plt.close(fig)
    provenance["figure_sha256"] = {p.name: sha256(p) for p in FIG.glob("*.png")}
    (FIG/"provenance.json").write_text(json.dumps(provenance,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")

if __name__ == "__main__":
    plot_samples()
