import numpy as np
import pandas as pd
from prepare_data import extract_rr_ref


def test_rr_reference_is_invariant_to_order_duplicates_and_nonfinite():
    clean = pd.DataFrame({'a': [0, 625, 1250, 1875],
                          'b': [0, 625, 1250, 1875]})
    dirty = pd.DataFrame({'a': [1250, 0, 625, 625, 1875, np.inf, np.nan],
                          'b': [1875, 1250, 625, 0, 1250, -np.inf, np.nan]})
    assert extract_rr_ref(clean, 0, 32) == 12.0
    assert extract_rr_ref(dirty, 0, 32) == 12.0


def test_duplicates_cannot_satisfy_three_unique_events():
    frame = pd.DataFrame({'a': [0, 625, 625], 'b': [0, 625, 625]})
    assert np.isnan(extract_rr_ref(frame, 0, 32))


def test_reference_window_is_half_open_and_consensus_is_inclusive():
    frame = pd.DataFrame({'a': [0, 750, 1500, 4000],
                          'b': [0, 625, 1250, 4000]})
    assert extract_rr_ref(frame, 0, 32, max_diff=2.0) == 11.0
    assert np.isnan(extract_rr_ref(frame, 0, 32, max_diff=1.999))
