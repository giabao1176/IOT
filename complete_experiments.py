"""Continue final training and evaluation after all CV workers finish."""
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch
import yaml

from run_experiments import ALL_CONFIGS, checkpoint_metadata, checkpoint_matches, compute_protocol_hash

ROOT = Path(__file__).resolve().parent


def main():
    logging.basicConfig(filename=ROOT / "completion.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    with (ROOT / "configs/c5.yaml").open(encoding="utf-8") as stream:
        cfg = yaml.safe_load(stream)
    protocol = compute_protocol_hash(cfg)
    worker_files = [ROOT / "checkpoints" / f"cv_worker_{suffix}.json" for suffix in ("01", "23", "45")]
    logging.info("Waiting for complete CV checkpoints matching %s", protocol)
    while True:
        complete = True
        result = {}
        for name, _, _, _, _, _ in ALL_CONFIGS:
            safe = name.replace(" ", "_").replace(":", "").replace("(", "").replace(")", "").replace("/", "_")
            folds = []
            for index in range(1, 6):
                path = ROOT / "checkpoints" / f"cv_{safe}_fold_{index}.pt"
                try:
                    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
                    expected = checkpoint_metadata(cfg, protocol, str(ROOT / cfg["data"]["splits_file"]),
                                                   str(ROOT / cfg["data"]["processed_dir"] / "bidmc_processed_dataset.npy"), name)
                    if not checkpoint_matches(checkpoint, expected) or checkpoint.get("training_complete") is not True:
                        complete = False
                        break
                    folds.append({"fold": index, "train_patients_count": len(checkpoint["train_pids"]),
                                  "val_patients_count": len(checkpoint["val_pids"]),
                                  "best_epoch": int(checkpoint["epoch"]), "best_val_loss": float(checkpoint["best_loss"]),
                                  "epochs_ran": checkpoint["epochs_ran"], "stop_reason": checkpoint["stop_reason"]})
                except (OSError, RuntimeError, EOFError):
                    complete = False
                    break
            if len(folds) == 5:
                losses = [fold["best_val_loss"] for fold in folds]
                result[name] = {"folds": folds, "median_best_epoch": int(np.median([fold["best_epoch"] for fold in folds])),
                                "val_loss_mean": float(np.mean(losses)), "val_loss_std": float(np.std(losses))}
        if complete and len(result) == len(ALL_CONFIGS) and all(path.exists() for path in worker_files):
            break
        time.sleep(30)
    with (ROOT / "checkpoints/cv_results.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    logging.info("All 30 CV folds validated; starting final stages")
    for stage in ("train_final", "evaluate", "benchmark"):
        logging.info("Starting %s", stage)
        subprocess.run([sys.executable, "run_experiments.py", "--config", "configs/c5.yaml", "--stage", stage, "--resume"],
                       cwd=ROOT, check=True)
        logging.info("Completed %s", stage)
    logging.info("Experiments completed. Report generation and visual review remain required.")


if __name__ == "__main__":
    main()
