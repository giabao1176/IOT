"""Replay the fixed RR reader on saved models; never train or tune parameters."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
import yaml

from evaluate import reconstruct_block_with_model
from models.autoencoder import PPGAutoencoder
from utils.estimators import estimate_rr_riiv_riav, rr_options
from utils.artifact_integrity import validate_saved_checkpoint


def nonoverlap_test_contexts(base, cfg):
    splits = json.loads((base / cfg['data']['splits_file']).read_text(encoding='utf-8'))
    records = np.load(base / cfg['data']['processed_dir'] / 'bidmc_processed_dataset.npy', allow_pickle=True)
    selected = []
    for pid in sorted(splits['test_pids']):
        last_end = -1.0
        for record in records:
            if record['pid'] == pid and record['t_start'] >= last_end - 1e-4:
                selected.append(record)
                last_end = record['t_end']
    return selected


def inspect(base, output, device):
    cfg = yaml.safe_load((base / 'configs/c5.yaml').read_text(encoding='utf-8'))
    rr_cfg = cfg['evaluation']['rr_estimator']
    models = {}
    for slug, name, lz in [('De_xuat_Day_du_8x', 'best_model_8x.pt', 115),
                           ('De_xuat_Day_du_16x', 'best_model_16x.pt', 52)]:
        model = PPGAutoencoder(lz)
        ckpt = torch.load(base / 'checkpoints' / name, map_location='cpu', weights_only=False)
        validate_saved_checkpoint(base / 'checkpoints' / name, ckpt)
        assert ckpt['trained_on_all_dev'] and ckpt['lz'] == lz
        model.load_state_dict(ckpt['model_state_dict'])
        models[slug] = model.to(device).eval()
    rows = []
    for index, context in enumerate(nonoverlap_test_contexts(base, cfg)):
        signals = {'Uncompressed': np.concatenate([b['signal_raw'] for b in context['blocks']])}
        for slug, model in models.items():
            lz = model.encoder.lz
            reconstructed = [reconstruct_block_with_model(
                model, b['signal_norm'], b['mu'], b['s'], lz,
                1 if lz == 115 else 2, index * 4 + j, device)[0]
                for j, b in enumerate(context['blocks'])]
            signals[slug] = np.concatenate(reconstructed)
        for slug, signal in signals.items():
            estimate, status = estimate_rr_riiv_riav(
                signal, fs=cfg['data']['sampling_rate'], **rr_options(cfg))
            row = {'ctx_idx': index, 'pid': int(context['pid']), 'subject_id': context['subject_id'], 't_start': context['t_start'],
                   't_end': context['t_end'], 'rr_ref': float(context['rr_ref']),
                   'method': slug, 'rr_est': estimate, **status}
            rows.append({k: None if isinstance(v, (float, np.floating)) and not np.isfinite(v)
                         else v for k, v in row.items()})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'scope': 'post_hoc_diagnostic_no_tuning', 'device': device, 'reader_config': rr_cfg,
                                 'rows': rows}, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(f"RR reader diagnostics: {len(rows)} rows -> {output}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, default=Path('reports/rr_analysis/reader_diagnostics.json'))
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cuda' if torch.cuda.is_available() else 'cpu')
    args = parser.parse_args()
    torch.set_num_threads(1)
    inspect(args.base.resolve(), args.output.resolve(), args.device)
