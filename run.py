"""Single entry point for the C5 research and replay workflows."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
COMMANDS = {
    'experiments': 'run_experiments',
    'demo': 'demo_pipeline',
    'verify': 'verify_delivery',
    'download': 'download_annotations',
    'prepare': 'prepare_data',
    'baseline': 'evaluate_dev_uncompressed',
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print('Usage: python run.py {' + ','.join(COMMANDS) + '} [options]')
        print('Replay: python run.py verify; demo: python run.py demo --level 8x')
        raise SystemExit(0 if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help') else 2)
    command = COMMANDS[sys.argv.pop(1)]
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'pipeline'))
    sys.path.insert(0, str(ROOT))
    runpy.run_module(command, run_name='__main__')

if __name__ == '__main__':
    main()
