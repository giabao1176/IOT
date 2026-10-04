"""Preserve the supplied run without rewriting checkpoint metadata."""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ledger = BASE / 'checkpoints/existing_run_evidence.json'
    if ledger.exists():
        raise RuntimeError('Existing-run evidence already exists; never silently replace it')
    archive = BASE / 'reports/history' / ('before_repair_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    archive.mkdir(parents=True, exist_ok=False)
    files = list(BASE.glob('*.py')) + list(BASE.glob('*.docx')) + list(BASE.glob('*.pdf'))
    files += [BASE / n for n in ['README.md', 'manifest.json', 'results_summary.json', 'execution.log']]
    for folder in ['models', 'utils', 'configs', 'tests']:
        files += [p for p in (BASE / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += list((BASE / 'checkpoints').glob('*.pt'))
    files += list((BASE / 'checkpoints').glob('*.json'))
    files += list(BASE.glob('results_*.csv'))
    files += list((BASE / 'data/processed').glob('*.json'))
    files += [BASE / 'data/splits_subject.json', BASE / 'reports/delivery_document_qa.json']
    checksums = {}
    for path in files:
        if not path.is_file():
            continue
        relative = path.relative_to(BASE)
        target = archive / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        checksums[relative.as_posix()] = digest(path)
        assert digest(target) == checksums[relative.as_posix()]
    evidence = {
        'schema_version': 1,
        'scope': 'Exact supplied checkpoint bytes admitted for inference replay only, not training resume',
        'training_origin_verified': False,
        'reason': 'User-supplied log records bulk replacement of source_code_hash after training. Original training-source hashes cannot be recovered from the overwritten fields.',
        'archive': archive.relative_to(BASE).as_posix(),
        'checksums': checksums,
        'checkpoint_sha256': {p.name: digest(p) for p in (BASE / 'checkpoints').glob('*.pt')},
        'locked_inputs': {n: digest(BASE / n) for n in ['configs/c5.yaml', 'data/splits_subject.json', 'data/processed/bidmc_processed_dataset.npy']},
        'disclosure_required': True,
    }
    ledger.write_text(json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    (archive / 'CAPTURE.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(archive)

if __name__ == '__main__':
    main()
