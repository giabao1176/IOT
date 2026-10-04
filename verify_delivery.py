"""Verify existing models in a new clean workspace. Never invokes training."""
import csv
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import torch

BASE = Path(__file__).resolve().parent
REPORT = 'Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien'

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def copy_inputs(destination):
    destination.mkdir(parents=True, exist_ok=False)
    for source in BASE.glob('*.py'):
        shutil.copy2(source, destination / source.name)
    for name in ['configs', 'models', 'utils', 'tests', 'data']:
        shutil.copytree(BASE / name, destination / name,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    (destination / 'checkpoints').mkdir()
    for source in (BASE / 'checkpoints').iterdir():
        if source.is_file(): shutil.copy2(source, destination / 'checkpoints' / source.name)
    for pattern in ['results_*.csv', 'results_summary.json', 'sample_packet*', 'manifest.json', 'README.md', 'requirements.txt', 'pytest.ini']:
        for source in BASE.glob(pattern): shutil.copy2(source, destination / source.name)
    shutil.copy2(BASE / 'DangGiaHuy_DeCuong_IoT_DaChinhSua2.docx', destination)
    return destination

def numeric_difference(original, reproduced, trail=''):
    differences = []
    if isinstance(original, dict):
        assert set(original) == set(reproduced), trail
        for key in original: differences += numeric_difference(original[key], reproduced[key], trail + '/' + str(key))
    elif isinstance(original, list):
        assert len(original) == len(reproduced), trail
        for index, (a, b) in enumerate(zip(original, reproduced)):
            differences += numeric_difference(a, b, trail + '/' + str(index))
    elif isinstance(original, (int, float)) and not isinstance(original, bool):
        if math.isnan(original): assert math.isnan(reproduced), trail
        elif math.isinf(original): assert original == reproduced, trail
        else:
            difference = abs(original - reproduced)
            assert difference <= 1e-4, (trail, original, reproduced)
            differences.append(difference)
    else: assert original == reproduced, (trail, original, reproduced)
    return differences

def csv_difference(a, b):
    with a.open(encoding='utf-8-sig', newline='') as f: left = list(csv.DictReader(f))
    with b.open(encoding='utf-8-sig', newline='') as f: right = list(csv.DictReader(f))
    assert len(left) == len(right)
    maximum = 0.0
    for i, (x, y) in enumerate(zip(left, right)):
        assert x.keys() == y.keys()
        for key in x:
            if x[key] == y[key]: continue
            try:
                difference = abs(float(x[key]) - float(y[key]))
            except ValueError:
                raise AssertionError((i, key, x[key], y[key]))
            exact = key in ['pid', 'subject_id', 'record_ids', 'context_idx', 'block_idx', 'method', 't_start', 't_end'] or key.startswith('n_') or key.endswith('_valid') or key.endswith('_nfft') or key.endswith('_nperseg')
            places = max(len(v.partition('.')[2]) for v in [x[key], y[key]])
            tolerance = 0.0 if exact else 10 ** (-places) + 1e-10
            assert math.isfinite(difference) and difference <= tolerance, (i, key, x[key], y[key])
            maximum = max(maximum, difference)
    return {'rows': len(left), 'max_numeric_difference': maximum}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cuda' if torch.cuda.is_available() else 'cpu')
    args = parser.parse_args()
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    evidence = BASE / 'reports' / 'reproducibility' / stamp
    evidence.mkdir(parents=True, exist_ok=False)
    workspace = copy_inputs(evidence / 'workspace')
    (workspace / 'reports').mkdir(exist_ok=True)
    before = {str(p.relative_to(workspace)): digest(p) for folder in ['checkpoints', 'data']
              for p in (workspace / folder).rglob('*') if p.is_file()}
    actions = []
    def run(name, args):
        print(name, flush=True)
        completed = subprocess.run([sys.executable, '-X', 'utf8', *args], cwd=workspace,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        (evidence / (name + '.log')).write_text(completed.stdout, encoding='utf-8')
        actions.append({'name': name, 'args': args, 'returncode': completed.returncode})
        if completed.returncode: raise RuntimeError(f'{name} failed; inspect {evidence}')
    run('tests', ['-m', 'pytest', '-q', '--basetemp', str(workspace / 'reports' / 'pytest_temporary')])
    run('exported_models', ['-c', "import torch; from export_encoder import export_encoder_model; torch.set_num_threads(1); torch.manual_seed(2026); x=torch.randn(4,1,1000); pairs=[('8x',115),('16x',52)]; [(export_encoder_model('checkpoints/best_model_'+level+'.pt','reports/reexport/encoder_'+level+'.pt',lz),torch.testing.assert_close(torch.jit.load('reports/reexport/encoder_'+level+'.pt')(x),torch.jit.load('checkpoints/ppg_encoder_'+level+'_traced.pt')(x),rtol=1e-5,atol=1e-6)) for level,lz in pairs]; print('Both re-exported encoders match saved encoders')"])
    run('demo_8x', ['demo_pipeline.py', '--level', '8x'])
    run('demo_16x', ['demo_pipeline.py', '--level', '16x'])
    run('evaluation', ['-c', f"import sys,torch; torch.set_num_threads(8); sys.argv=['run_experiments.py','--stage','evaluate','--device','{args.device}']; from run_experiments import main; main()"])
    original = json.loads((BASE / 'results_summary.json').read_text(encoding='utf-8'))
    reproduced = json.loads((workspace / 'results_summary.json').read_text(encoding='utf-8'))
    differences = numeric_difference(original['evaluation'], reproduced['evaluation'])
    csv_results = {name: csv_difference(BASE / name, workspace / name) for name in
                   ['results_per_context.csv', 'results_per_patient.csv', 'results_per_window.csv']}
    for relative, value in before.items(): assert digest(workspace / relative) == value, relative
    for path in (workspace / 'reports/demo').glob('*.json'):
        shutil.copy2(path, evidence / path.name)
    verification = {'scope': 'clean_file_workspace_same_installed_runtime_not_fresh_dependency_install',
                    'python_executable': sys.executable, 'python_version': sys.version,
                    'workspace': str(workspace), 'training_invoked': False, 'actions': actions,
                    'evaluation_numeric_values_compared': len(differences),
                    'evaluation_max_absolute_difference': max(differences, default=0),
                    'evaluation_absolute_tolerance': 1e-4, 'csv_checks': csv_results,
                    'evaluation_intraop_threads': 8,
                    'evaluation_device': args.device,
                    'input_and_checkpoint_hashes_unchanged': True, 'input_sha256': before,
                    'source_sha256': {p.relative_to(BASE).as_posix(): digest(p) for p in list(BASE.glob('*.py')) + [BASE/n for n in ['README.md','requirements.txt','pytest.ini']] + [p for f in ['models', 'utils', 'tests', 'configs'] for p in (BASE/f).rglob('*') if p.is_file() and '__pycache__' not in p.parts]},
                    'result_sha256': {name: digest(BASE/name) for name in ['results_summary.json', 'results_per_context.csv', 'results_per_patient.csv', 'results_per_window.csv']},
                    'report_generation': 'Verified separately on final artifact; this run verifies evaluation and tables only.'}
    (evidence / 'verification.json').write_text(json.dumps(verification, indent=2, ensure_ascii=False), encoding='utf-8')
    (BASE / 'reports/reproducibility/latest.json').write_text(json.dumps({'evidence': str(evidence)}, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in verification.items() if k != 'input_sha256'}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
