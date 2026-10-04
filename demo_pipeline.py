"""Demonstrate saved encoders, causal raw filtering, real packets and localhost UDP. No training."""
import argparse
import hashlib
import json
import socket
from pathlib import Path
import numpy as np
from scipy.signal import sosfilt
import torch
import yaml
from models.autoencoder import PPGAutoencoder
from utils.deployment_reference import configured_sos, load_raw_benchmark_blocks
from utils.estimators import estimate_hr_peaks, estimate_rr_riiv_riav, rr_options
from utils.artifact_integrity import validate_saved_checkpoint
from utils.packet_codec import quantize_latent, dequantize_latent, pack_payload, unpack_payload, PacketSequenceTracker

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level', choices=['8x', '16x'], default='8x')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    cfg = yaml.safe_load((base / 'configs/c5.yaml').read_text(encoding='utf-8'))
    splits = json.loads((base / cfg['data']['splits_file']).read_text(encoding='utf-8'))
    lz = cfg['codec']['levels'][args.level]['lz']
    checkpoint = base / f'checkpoints/best_model_{args.level}.pt'
    exported = base / f'checkpoints/ppg_encoder_{args.level}_traced.pt'
    saved = torch.load(checkpoint, map_location='cpu', weights_only=False)
    validate_saved_checkpoint(checkpoint, saved)
    assert saved['trained_on_all_dev'] and saved['lz'] == lz
    model = PPGAutoencoder(lz).eval()
    model.load_state_dict(saved['model_state_dict'])
    encoder = torch.jit.load(str(exported), map_location='cpu').eval()
    blocks, sources = load_raw_benchmark_blocks(str(base), cfg, splits['test_pids'], minimum=4)
    selected = blocks[:4]
    assert len({b['pid'] for b in selected}) == 1
    assert all(selected[i + 1]['start_sample'] - selected[i]['start_sample'] == 1000 for i in range(3))
    packets, restored, details = [], [], []
    config_id = 1 if args.level == '8x' else 2
    torch.set_num_threads(1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender, torch.inference_mode():
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(2)
        for sequence, block in enumerate(selected):
            filtered = sosfilt(configured_sos(cfg), block['signal'], zi=block['filter_state'])[0].astype(np.float32)
            mu, s = float(filtered.mean()), max(float(filtered.std()), cfg['data']['normalization']['eps'])
            x = torch.from_numpy((filtered - mu) / s).reshape(1, 1, 1000)
            encoded = encoder(x)
            torch.testing.assert_close(encoded, model.encoder(x), rtol=1e-5, atol=1e-6)
            z, _ = encoded
            q, a = quantize_latent(z.numpy().ravel())
            payload = pack_payload(1, config_id, sequence, mu, s, a, q)
            sender.sendto(payload, receiver.getsockname())
            received, _ = receiver.recvfrom(4096)
            assert received == payload
            decoded = unpack_payload(received, expected_lz=lz, expected_config_id=config_id)
            assert decoded['valid'] and decoded['crc_valid'] and decoded['version'] == 1
            assert len(payload) == 20 + 2 * lz
            latent = torch.from_numpy(dequantize_latent(decoded['payload_int16'], decoded['a'])).reshape(1, lz)
            waveform = model.decoder(latent).numpy().ravel() * np.float32(decoded['s']) + np.float32(decoded['mu'])
            assert len(waveform) == 1000 and np.isfinite(waveform).all()
            restored.append(waveform)
            packets.append(decoded)
            details.append({'sequence': sequence, 'packet_bytes': len(payload), 'crc_valid': True,
                            'packet_sha256': hashlib.sha256(payload).hexdigest(),
                            'hr_bpm': float(estimate_hr_peaks(waveform, fs=125))})
    assert PacketSequenceTracker.can_form_context(packets)
    rr_cfg = cfg['evaluation']['rr_estimator']
    rr, status = estimate_rr_riiv_riav(np.concatenate(restored), fs=125, **rr_options(cfg))
    result = {'level': args.level, 'pid': selected[0]['pid'], 'sources': sources,
              'start_samples': [b['start_sample'] for b in selected],
              'scope': 'CPU_reference_localhost_UDP_no_hardware_or_radio_measurement',
              'encoder_sha256': hashlib.sha256(exported.read_bytes()).hexdigest(),
              'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              'packets': details, 'contiguous_rr_context': True, 'rr_bpm': float(rr), 'rr_status': status}
    def finite(value):
        if isinstance(value, dict): return {k: finite(v) for k, v in value.items()}
        if isinstance(value, list): return [finite(v) for v in value]
        if isinstance(value, (float, np.floating)) and not np.isfinite(value): return None
        return value
    result = finite(result)
    output = args.output or base / f'reports/demo/demo_{args.level}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))

if __name__ == '__main__':
    main()
