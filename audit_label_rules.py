"""Read-only comparison of label validity against the approved proposal."""
from pathlib import Path
import numpy as np
import pandas as pd
from prepare_data import extract_rr_ref

def main():
    raw = Path(__file__).resolve().parent / "data/raw_bidmc"
    differences = []
    for path in sorted(raw.glob("*_Breaths.csv")):
        frame = pd.read_csv(path)
        for start in range(32, 449, 4):
            strict = extract_rr_ref(frame, start, start + 32, max_diff=2.0)
            current = extract_rr_ref(frame, start, start + 32, max_diff=3.0)
            if np.isnan(strict) != np.isnan(current):
                differences.append((path.name, start, strict, current))
    total = outside = 0
    for path in sorted(raw.glob("*_Numerics.csv")):
        frame = pd.read_csv(path)
        column = next(c for c in frame.columns if "hr" in c.lower())
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy()
        valid = np.isfinite(values) & (values > 0)
        total += int(valid.sum())
        outside += int((valid & ((values < 30) | (values > 210))).sum())
    print("RR contexts whose label validity differs:", len(differences))
    print("Examples:", differences[:5])
    print("Finite positive HR values:", total, "outside search range:", outside)

if __name__ == "__main__":
    main()
