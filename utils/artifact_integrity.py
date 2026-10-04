"""Immutable supplied-model replay evidence; never repairs training hashes."""
import hashlib
import json
import math
from pathlib import Path

def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def validate_saved_checkpoint(path, checkpoint, expected=None):
    path = Path(path).resolve()
    base = path.parent.parent
    if not checkpoint.get('trained_on_all_dev') or int(checkpoint.get('epoch', 0)) < 1:
        raise ValueError('Only completed full-development checkpoints may be exported or evaluated')
    if expected and any(checkpoint.get(k) != v for k, v in expected.items() if k != 'source_code_hash'):
        raise ValueError('Checkpoint data, split, configuration or protocol mismatch')
    ledger_path = path.parent / 'existing_run_evidence.json'
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
        if path.name in ledger['checkpoint_sha256']:
            if sha256(path) != ledger['checkpoint_sha256'][path.name]:
                raise ValueError('Supplied checkpoint bytes changed; replay evidence is invalid')
            for relative, digest in ledger['locked_inputs'].items():
                if sha256(base / relative) != digest:
                    raise ValueError('Supplied-run input changed: ' + relative)
            return 'supplied_run_replay_training_origin_not_verified'
    if not expected or checkpoint.get('source_code_hash') != expected.get('source_code_hash'):
        raise ValueError('Checkpoint lacks matching immutable replay or current training-source evidence')
    return 'current_training_metadata'

def strict_json(value):
    """Nonfinite values become null; callers must attach an explicit semantic status."""
    if isinstance(value, dict):
        return {str(k): strict_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [strict_json(v) for v in value]
    if hasattr(value, 'tolist'):
        return strict_json(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value

def stage_fingerprint(base, config, checkpoint_paths=()):
    base = Path(base)
    sources = list(base.glob('*.py'))
    for directory in ['pipeline', 'models', 'utils']:
        sources += list((base / directory).glob('*.py'))
    inputs = sources + [base / 'configs/c5.yaml', base / config['data']['splits_file'],
                       base / config['data']['processed_dir'] / 'bidmc_processed_dataset.npy']
    inputs += [Path(p) for p in checkpoint_paths]
    inputs += [p for p in (base / config['data']['raw_dir']).iterdir() if p.is_file()]
    inputs += [base / 'checkpoints/cv_results.json', base / 'checkpoints/existing_run_evidence.json',
               base / 'data/processed/dev_uncompressed_baseline.json']
    evidence = {p.relative_to(base).as_posix(): sha256(p) for p in sorted(set(inputs))}
    raw = json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'input_checksums': evidence}

def validate_fixed_configuration(cfg):
    fixed = [(['data', 'sampling_rate'], 125), (['data', 'window_samples'], 1000),
             (['data', 'context_samples'], 4000), (['codec', 'fixed_header_bytes'], 20),
             (['codec', 'quantization', 'scale_min'], 1e-8),
             (['codec', 'quantization', 'clip_min'], -32767),
             (['codec', 'quantization', 'clip_max'], 32767)]
    encoder = dict(e1_channels=16, e1_kernel=7, e1_stride=2, e1_padding=3,
                   e2_channels=32, e2_kernel=7, e2_stride=2, e2_padding=3,
                   e3_channels=1, e3_kernel=1, e3_stride=1, bottleneck_in=250)
    decoder = dict(d1_out=250, d2_channels=32, d2_kernel=1, d3_channels=16,
                   d3_kernel=7, d3_stride=2, d3_padding=3, d3_output_padding=1,
                   d4_channels=1, d4_kernel=7, d4_stride=2, d4_padding=3, d4_output_padding=1)
    fixed += [(['model', 'in_channels'], 1), (['model', 'encoder'], encoder), (['model', 'decoder'], decoder)]
    for path, expected in fixed:
        value = cfg
        for key in path:
            value = value[key]
        if value != expected:
            raise ValueError('Unsupported fixed implementation configuration: ' + '.'.join(path))

def validate_stage_fingerprint(base, evidence):
    if not evidence:
        raise ValueError('Missing stage fingerprint')
    for relative, expected in evidence['input_checksums'].items():
        if sha256(Path(base) / relative) != expected:
            raise ValueError('Stale result: ' + relative)
