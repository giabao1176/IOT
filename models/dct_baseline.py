import numpy as np
from scipy.fftpack import dct, idct
from utils.packet_codec import quantize_latent, dequantize_latent, pack_payload, unpack_payload

class DCTBaseline:
    def __init__(self, lz: int = 115):
        self.lz = lz

    def encode(self, x_norm: np.ndarray, version: int = 1, config_id: int = 1, 
               seq: int = 0, mu: float = 0.0, s: float = 1.0) -> bytes:
        x_1d = x_norm.reshape(-1)
        coeffs = dct(x_1d, type=2, norm='ortho')
        z = coeffs[:self.lz]
        q, a = quantize_latent(z)
        packet = pack_payload(version, config_id, seq, mu, s, a, q)
        return packet

    def decode(self, packet_bytes: bytes, target_len: int = 1000, expected_config_id: int = None) -> tuple[np.ndarray | None, dict]:
        unpacked = unpack_payload(packet_bytes, expected_lz=self.lz, expected_config_id=expected_config_id)
        if not unpacked.get("valid", False) or not unpacked.get("crc_valid", False):
            return None, unpacked

        q = unpacked["payload_int16"]
        a = unpacked["a"]
        mu = unpacked["mu"]
        s = unpacked["s"]
        
        z_rec = dequantize_latent(q, a)
        coeffs_full = np.zeros(target_len, dtype=np.float32)
        coeffs_full[:len(z_rec)] = z_rec
        x_rec_norm = idct(coeffs_full, type=2, norm='ortho')
        x_rec = x_rec_norm * s + mu
        return x_rec.reshape(1, 1, target_len), unpacked
