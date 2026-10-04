"""Raw-stream CPU reference measurements; never consume processed PPG here."""
import os
import hashlib
import numpy as np
from scipy.signal import butter, sosfilt


def configured_sos(cfg):
    data = cfg["data"]
    filt = data["filter"]
    return butter(filt["order_n"], [filt["low_hz"], filt["high_hz"]],
                  btype="bandpass", fs=data["sampling_rate"], output="sos")


def stream_blocks(raw, pid, cfg):
    """Each block carries the exact causal state of its finite raw segment.

    State is reset at patient boundaries and gaps. Warmup is discarded after
    every reset. Precomputed states allow identical inputs for both encoders.
    """
    raw = np.asarray(raw, dtype=np.float32)
    sos = configured_sos(cfg)
    size = int(cfg["data"]["window_samples"])
    warm = round(cfg["data"]["warmup_seconds"] * cfg["data"]["sampling_rate"])
    finite = np.isfinite(raw)
    edges = np.diff(np.r_[False, finite, False].astype(int))
    blocks = []
    for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        if end - start < warm + size:
            continue
        zi = np.zeros((len(sos), 2), dtype=np.float64)
        _, zi = sosfilt(sos, raw[start:start + warm], zi=zi)
        for offset in range(start + warm, end - size + 1, size):
            signal = raw[offset:offset + size].copy()
            blocks.append({"pid": int(pid), "start_sample": int(offset),
                           "segment_start_sample": int(start), "signal": signal,
                           "filter_state": zi.copy()})
            _, zi = sosfilt(sos, signal, zi=zi)
    return blocks


def load_raw_benchmark_blocks(base_dir, cfg, pids, minimum=120):
    import wfdb
    blocks = []
    sources = []
    for pid in sorted(pids):
        path = os.path.join(base_dir, cfg["data"]["raw_dir"], f"bidmc{int(pid):02d}")
        record = wfdb.rdrecord(path)
        if record.fs != cfg["data"]["sampling_rate"]:
            raise ValueError("Raw sampling rate differs from the locked configuration")
        names = [name.strip().replace(",", "") for name in record.sig_name]
        if "PLETH" not in names:
            raise ValueError(f"Missing PLETH channel: {path}")
        blocks.extend(stream_blocks(record.p_signal[:, names.index("PLETH")], pid, cfg))
        for suffix in (".hea", ".dat"):
            with open(path + suffix, "rb") as source:
                sources.append({"file": os.path.relpath(path + suffix, base_dir),
                                "sha256": hashlib.sha256(source.read()).hexdigest()})
        if len(blocks) >= minimum:
            break
    if len(blocks) < minimum:
        raise ValueError(f"Need {minimum} raw blocks, found {len(blocks)}")
    return blocks, sources


def sustained_settling_time(response, fs, fraction=0.01):
    """First time after the final threshold violation in the observed horizon."""
    amplitude = np.abs(np.asarray(response, dtype=np.float64))
    if not np.all(np.isfinite(amplitude)) or not len(amplitude):
        raise ValueError("Response must be finite and nonempty")
    limit = fraction * float(amplitude.max())
    violations = np.flatnonzero(amplitude > limit)
    if not len(violations):
        return 0.0
    next_sample = int(violations[-1]) + 1
    return next_sample / float(fs) if next_sample < len(amplitude) else None
