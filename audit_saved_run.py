"""Audit immutable supplied models and repair descriptive CV counts, without training."""
import json
from pathlib import Path
import torch
from utils.artifact_integrity import sha256
from run_experiments import ALL_CONFIGS

BASE = Path(__file__).resolve().parent

def main():
    ledger = json.loads((BASE/'checkpoints/existing_run_evidence.json').read_text(encoding='utf-8'))
    splits = json.loads((BASE/'data/splits_subject.json').read_text(encoding='utf-8'))
    cvpath = BASE/'checkpoints/cv_results.json'
    cv = json.loads(cvpath.read_text(encoding='utf-8'))
    rows = []
    for name, filename, lz, beta, hr, rr in ALL_CONFIGS:
        safe = name.replace(' ','_').replace(':','').replace('(','').replace(')','').replace('/','_')
        for detail in cv[name]['folds']:
            f = detail['fold']
            path = BASE/'checkpoints'/f'cv_{safe}_fold_{f}.pt'
            assert sha256(path) == ledger['checkpoint_sha256'][path.name]
            saved = torch.load(path,map_location='cpu',weights_only=False)
            split = splits['folds'][f'fold_{f}']
            assert saved['training_complete'] is True
            assert saved['epoch'] == detail['best_epoch']
            assert abs(saved['best_loss'] - detail['best_val_loss']) < 1e-12
            assert sorted(saved['train_pids']) == sorted(split['train_pids'])
            assert sorted(saved['val_pids']) == sorted(split['val_pids'])
            assert not set(split['train_subjects']) & set(split['val_subjects'])
            detail['train_records_count'] = len(split['train_pids'])
            detail['val_records_count'] = len(split['val_pids'])
            detail['train_patients_count'] = len(split['train_subjects'])
            detail['val_patients_count'] = len(split['val_subjects'])
            rows.append(dict(checkpoint=path.name,fold=f,bytes_unchanged=True,membership_matches=True))
        final = BASE/'checkpoints'/filename
        saved = torch.load(final,map_location='cpu',weights_only=False)
        assert sha256(final) == ledger['checkpoint_sha256'][filename]
        assert saved['trained_on_all_dev'] and saved['epoch'] == cv[name]['median_best_epoch']
        assert len(saved['history']) == saved['epoch']
        assert (saved['lz'],saved['beta'],saved['hr_weight'],saved['rr_weight']) == (lz,beta,hr,rr)
    cvpath.write_text(json.dumps(cv,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    proof=dict(cv_checkpoints=len(rows),final_checkpoints=6,training_invoked=False,
               checkpoint_bytes_unchanged=True,original_training_source_verified=False,
               scope='Checkpoint metadata and supplied bytes checked; hash-overwrite provenance gap remains disclosed',
               fold_membership=rows)
    (BASE/'reports/checkpoint_replay_audit.json').write_text(json.dumps(proof,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(proof,indent=2))

if __name__=='__main__':
    main()
