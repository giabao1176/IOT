"""Versioned RR reference revision without altering immutable training inputs.

The saved NPY is preserved for checkpoint provenance. RR references are recomputed
from raw annotations when loading it. No waveform, normalization or HR label is
modified; RR references do not enter the waveform/spectral training objective.
"""
from pathlib import Path
import numpy as np
import pandas as pd


def load_records_with_current_rr(data_path, raw_dir, fs=125, max_diff=2.0):
    from pipeline.prepare_data import extract_rr_ref

    records = np.load(data_path, allow_pickle=True)
    annotations = {}
    for record in records:
        pid = int(record['pid'])
        if pid not in annotations:
            annotations[pid] = pd.read_csv(Path(raw_dir) / f'bidmc_{pid:02d}_Breaths.csv')
        record['rr_ref'] = extract_rr_ref(
            annotations[pid], record['t_start'], record['t_end'], fs=fs,
            max_diff=max_diff)
    return records
