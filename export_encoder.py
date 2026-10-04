import os
import torch
from models.autoencoder import PPGAutoencoder, PPGEncoder
from utils.artifact_integrity import validate_saved_checkpoint

def export_encoder_model(checkpoint_path: str, out_path: str, lz: int = 115):
    if not os.path.isfile(checkpoint_path):
        raise FileNotFoundError(f"Không tìm thấy checkpoint: {checkpoint_path}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    device = torch.device('cpu')
    
    model = PPGAutoencoder(lz=lz)
    if os.path.exists(checkpoint_path):
        ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
        import yaml
        from run_experiments import checkpoint_metadata, compute_protocol_hash
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs', 'c5.yaml'), encoding='utf-8') as stream:
            cfg = yaml.safe_load(stream)
        base = os.path.dirname(os.path.abspath(__file__))
        expected = checkpoint_metadata(cfg, compute_protocol_hash(cfg),
            os.path.join(base, cfg['data']['splits_file']),
            os.path.join(base, cfg['data']['processed_dir'], 'bidmc_processed_dataset.npy'), ckpt.get('config_name'))
        validate_saved_checkpoint(checkpoint_path, ckpt, expected)
        if ckpt.get('lz') != lz:
            raise ValueError('Checkpoint latent dimension mismatch')
        model.load_state_dict(ckpt["model_state_dict"])
        print(f"Loaded weights from {checkpoint_path}")
        
    encoder = model.encoder.eval()
    dummy_input = torch.randn(1, 1, 1000, dtype=torch.float32)
    
    # Xuat TorchScript
    traced_encoder = torch.jit.trace(encoder, dummy_input)
    traced_encoder.save(out_path)
    
    file_size_bytes = os.path.getsize(out_path)
    file_size_kb = file_size_bytes / 1024.0
    print(f"Exported TorchScript encoder to: {out_path}")
    print(f"File size: {file_size_bytes} bytes ({file_size_kb:.2f} KiB)")
    return out_path, file_size_kb

if __name__ == "__main__":
    ckpt = "checkpoints/best_model_8x.pt"
    out_pt = "checkpoints/encoder_8x_traced.pt"
    export_encoder_model(ckpt, out_pt, lz=115)
