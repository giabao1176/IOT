import numpy as np
from scipy.signal import sosfilt
from utils.deployment_reference import configured_sos, stream_blocks, sustained_settling_time


def config():
    return {"data": {"sampling_rate": 125, "window_samples": 1000,
                     "warmup_seconds": 32,
                     "filter": {"order_n": 2, "low_hz": 0.05, "high_hz": 8.0}}}


def test_raw_blocks_match_continuous_filter_and_reset_after_gap():
    cfg = config()
    rng = np.random.default_rng(41)
    raw = rng.normal(size=16000).astype(np.float32)
    raw[7000:8000] = np.nan
    blocks = stream_blocks(raw, 1, cfg)
    sos = configured_sos(cfg)
    for block in blocks:
        start = block["segment_start_sample"]
        offset = block["start_sample"]
        reference = sosfilt(sos, raw[start:offset + 1000])[-1000:]
        actual, _ = sosfilt(sos, block["signal"], zi=block["filter_state"])
        np.testing.assert_allclose(actual, reference, atol=1e-12)
        np.testing.assert_array_equal(block["signal"], raw[offset:offset + 1000])
        assert offset >= start + 4000
    other_patient = stream_blocks(raw, 2, cfg)
    np.testing.assert_array_equal(blocks[0]["filter_state"], other_patient[0]["filter_state"])
    assert [b["start_sample"] for b in blocks] == [4000, 5000, 6000, 12000, 13000, 14000, 15000]


def test_settling_uses_last_violation_not_first_crossing():
    response = [1.0, 0.001, 0.2, 0.001, 0.001]
    assert sustained_settling_time(response, 10) == 0.3
    assert sustained_settling_time([1.0, 0.001, 0.2], 10) is None
    assert sustained_settling_time(np.zeros(10), 10) == 0.0
