import json
import numpy as np
import pytest
import torch
import yaml
from pathlib import Path
from utils.packet_codec import pack_payload
from utils.artifact_integrity import strict_json, validate_fixed_configuration, validate_saved_checkpoint
from utils.estimators import rr_options
from utils.spectral_loss import MultiScaleSpectralLoss
from train import train_epoch, evaluate_loss
from verify_delivery import csv_difference

def test_float32_underflow_overflow_and_payload_range():
    q = np.zeros(115, dtype=np.int16)
    for s in [1e-50, 1e50]:
        with pytest.raises(ValueError):
            pack_payload(1, 1, 0, 0., s, 1e-8, q)
    with pytest.raises(ValueError):
        pack_payload(1, 1, 0, 0., 1., 1e-8, np.array([32768]))

def test_strict_json_semantics():
    cleaned = strict_json({'missing': np.nan, 'infinite': np.inf, 'zero': 0.0})
    assert cleaned == {'missing': None, 'infinite': None, 'zero': 0.0}
    json.dumps(cleaned, allow_nan=False)

def test_empty_loaders_and_invalid_scales():
    model = torch.nn.Linear(1, 1)
    with pytest.raises(ValueError):
        train_epoch(model, [], None, None, 'cpu')
    with pytest.raises(ValueError):
        evaluate_loss(model, [], None, 'cpu')
    loss = MultiScaleSpectralLoss()
    x = torch.zeros(4, 1, 1000)
    for scales in [torch.zeros(4), torch.full((4,), float('nan'))]:
        with pytest.raises(ValueError):
            loss(x, x, x.reshape(1,4000), x.reshape(1,4000), scales)

def test_all_reader_options_are_bound_and_fixed_model_checked():
    base = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((base/'configs/c5.yaml').read_text(encoding='utf-8'))
    opts = rr_options(cfg)
    assert opts['min_paired'] == 8 and opts['nfft'] == 2048 and opts['welch_window_sec'] == 16
    cfg['model']['encoder']['e1_channels'] = 17
    with pytest.raises(ValueError):
        validate_fixed_configuration(cfg)

def test_untrained_export_guard(tmp_path):
    with pytest.raises(ValueError):
        validate_saved_checkpoint(tmp_path/'x.pt', {'epoch': 1, 'trained_on_all_dev': False})

def test_csv_denominator_and_identifier_not_approximate(tmp_path):
    a, b = tmp_path/'a.csv', tmp_path/'b.csv'
    a.write_text('pid,n_blocks,prd_mean\n11,56,1.84\n')
    b.write_text('pid,n_blocks,prd_mean\n11.001,56,1.84\n')
    with pytest.raises(AssertionError):
        csv_difference(a,b)
