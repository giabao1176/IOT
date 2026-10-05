"""Independently audit the versioned RR references; never train or edit checkpoints."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from utils.artifact_integrity import sha256, strict_json
from utils.reference_labels import load_records_with_current_rr

BASE = Path(__file__).resolve().parents[1]


def independent_reference(frame, start, end, fs, tolerance):
    rates = []
    for col in frame.columns[:2]:
        events = sorted({float(v) for v in pd.to_numeric(frame[col], errors='coerce')
                         if np.isfinite(v) and int(start * fs) <= v < int(end * fs)})
        if len(events) < 3:
            return np.nan
        rates.append(60 * fs * (len(events) - 1) / (events[-1] - events[0]))
    return sum(rates) / 2 if abs(rates[0] - rates[1]) <= tolerance else np.nan


def main():
    cfg = yaml.safe_load((BASE / 'configs/c5.yaml').read_text(encoding='utf-8'))
    source = BASE / cfg['data']['processed_dir'] / 'bidmc_processed_dataset.npy'
    raw_dir = BASE / cfg['data']['raw_dir']
    original = np.load(source, allow_pickle=True)
    corrected = load_records_with_current_rr(source, raw_dir,
        cfg['data']['sampling_rate'], cfg['data']['label_rules']['rr_max_consensus_diff'])
    frames = {int(r['pid']): pd.read_csv(raw_dir / f"bidmc_{int(r['pid']):02d}_Breaths.csv")
              for r in original}
    changes = []
    for old, new in zip(original, corrected):
        ref = independent_reference(frames[int(old['pid'])], old['t_start'], old['t_end'],
            cfg['data']['sampling_rate'], cfg['data']['label_rules']['rr_max_consensus_diff'])
        assert np.isclose(new['rr_ref'], ref, equal_nan=True, rtol=0, atol=1e-12)
        for key in old:
            if key in ('blocks', 'rr_ref'): continue
            assert old[key] == new[key]
        for a, b in zip(old['blocks'], new['blocks']):
            for key in a:
                np.testing.assert_equal(a[key], b[key])
        if not np.isclose(old['rr_ref'], ref, equal_nan=True, rtol=0, atol=1e-12):
            changes.append({'pid': int(old['pid']), 'context_idx': int(old['context_idx']),
                't_start': old['t_start'], 'old_rr_ref': old['rr_ref'],
                'new_rr_ref': ref, 'is_test': bool(old['is_test'])})
    selected = []
    for pid in sorted(frames):
        last_end = -np.inf
        for record in corrected:
            if record['pid'] == pid and record['t_start'] >= last_end - 1e-4:
                selected.append(record)
                last_end = record['t_end']
    keys = {(r['pid'], r['context_idx']) for r in selected}
    nonoverlap_changes = [r for r in changes if (r['pid'], r['context_idx']) in keys]
    evidence = {'schema_version': 1, 'rr_reference_version': 'sorted_unique_finite_v1',
        'scope': 'Recomputed RR labels at load time; immutable saved NPY retained for provenance',
        'contexts_checked': len(original), 'blocks_checked': 4 * len(original),
        'independent_reference_matches': True, 'waveforms_normalization_hr_metadata_unchanged': True,
        'training_invoked': False, 'changes': changes, 'nonoverlapping_changes': nonoverlap_changes,
        'input_sha256': {source.relative_to(BASE).as_posix(): sha256(source),
                         **{p.relative_to(BASE).as_posix(): sha256(p) for p in sorted(raw_dir.glob('*Breaths.csv'))}},
        'checkpoint_sha256': {p.name: sha256(p) for p in sorted((BASE / 'checkpoints').glob('*.pt'))},
        'test_valid_reference_contexts': sum(np.isfinite(r['rr_ref']) for r in selected if r['is_test'])}
    out = BASE / 'evidence/rr_reference_revision'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'audit.json').write_text(json.dumps(strict_json(evidence), indent=2, ensure_ascii=False,
        allow_nan=False), encoding='utf-8')
    print(json.dumps(strict_json({k: v for k, v in evidence.items() if k not in ('input_sha256', 'checkpoint_sha256')}), indent=2))


if __name__ == '__main__':
    main()
