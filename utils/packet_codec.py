import struct
import binascii
import numpy as np

def compute_crc16(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    if poly == 0x1021:
        return binascii.crc_hqx(data, init)
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

def quantize_latent(z: np.ndarray) -> tuple[np.ndarray, float]:
    z_arr = np.asarray(z, dtype=np.float32).ravel()
    if not np.all(np.isfinite(z_arr)):
        raise ValueError("Vector không gian ẩn phải chỉ chứa số hữu hạn")
    max_val = float(np.max(np.abs(z_arr))) if len(z_arr) > 0 else 0.0
    scale_a = float(max(max_val / 32767.0, 1e-8))
    q = np.clip(np.round(z_arr / scale_a), -32767, 32767).astype(np.int16)
    return q, scale_a

def dequantize_latent(q: np.ndarray, scale_a: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=np.int16)
    return q_arr.astype(np.float32) * float(scale_a)

def pack_payload(version: int, config_id: int, sequence: int, 
                 mu: float, s: float, a: float, 
                 payload_int16: np.ndarray) -> bytes:
    if not (0 <= version <= 255):
        raise ValueError("Phiên bản version phải nằm trong khoảng [0, 255]")
    if not (0 <= config_id <= 255):
        raise ValueError("Config ID phải nằm trong khoảng [0, 255]")
    if not (0 <= sequence <= 0xFFFFFFFF):
        raise ValueError("Sequence phải là số nguyên không dấu 32-bit")
        
    mu_f = float(mu)
    s_f = float(s)
    a_f = float(a)
    if not (np.isfinite(mu_f) and np.isfinite(s_f) and np.isfinite(a_f)):
        raise ValueError("Các hệ số mu, s, a phải là số thực hữu hạn")
    if s_f <= 0.0:
        raise ValueError("Độ lệch chuẩn s phải là số dương")
    if a_f <= 0.0:
        raise ValueError("Hệ số tỷ lệ lượng tử a phải là số dương")
    with np.errstate(over='ignore', under='ignore'):
        mu_f, s_f, a_f = (float(v) for v in np.asarray([mu_f, s_f, a_f], dtype=np.float32))
    if not all(np.isfinite(v) for v in [mu_f, s_f, a_f]) or s_f <= 0 or a_f <= 0:
        raise ValueError("Các hệ số phải hữu hạn và s, a phải dương sau khi chuyển FLOAT32")
    values = np.asarray(payload_int16)
    if not np.all(np.isfinite(values)) or np.any(values != np.round(values)) or np.any(np.abs(values.astype(np.float64)) > 32767):
        raise ValueError("Tải trọng phải là số nguyên đối xứng trong [-32767, 32767]")
        
    payload_arr = np.asarray(payload_int16, dtype='<i2').ravel()
    payload_bytes = payload_arr.tobytes()
    
    # Header cố định 18 byte trước CRC
    header_without_crc = struct.pack(
        '<BBIfff',
        version,
        config_id,
        sequence,
        mu_f,
        s_f,
        a_f
    )
    data_to_checksum = header_without_crc + payload_bytes
    crc = compute_crc16(data_to_checksum)
    crc_bytes = struct.pack('<H', crc)
    return data_to_checksum + crc_bytes

def unpack_payload(packet_bytes: bytes, expected_lz: int = None, expected_config_id: int = None,
                   expected_version: int = 1) -> dict:
    res = {
        "valid": False,
        "error_msg": "",
        "version": None,
        "config_id": None,
        "sequence": None,
        "mu": None,
        "s": None,
        "a": None,
        "payload_int16": None,
        "crc_valid": False,
        "crc_received": None,
        "crc_calculated": None,
        "packet_len": len(packet_bytes)
    }
    
    if len(packet_bytes) < 20:
        res["error_msg"] = "Độ dài gói tin nhỏ hơn 20 byte"
        return res
        
    payload_and_crc_len = len(packet_bytes) - 18
    payload_len = payload_and_crc_len - 2
    if payload_len % 2 != 0:
        res["error_msg"] = "Chiều dài phần tải trọng không phải bội số của 2 byte"
        return res
        
    data_to_check = packet_bytes[:-2]
    crc_received = struct.unpack('<H', packet_bytes[-2:])[0]
    crc_calc = compute_crc16(data_to_check)
    res["crc_received"] = crc_received
    res["crc_calculated"] = crc_calc
    res["crc_valid"] = (crc_received == crc_calc)
    
    if not res["crc_valid"]:
        res["error_msg"] = "Sai mã kiểm tra CRC-16"
        return res
        
    header_data = packet_bytes[:18]
    version, config_id, sequence, mu, s, a = struct.unpack('<BBIfff', header_data)
    
    # Kiểm tra phiên bản gói tin: từ chối phiên bản khác dù CRC đúng
    if expected_version is not None and version != expected_version:
        res["error_msg"] = f"Phiên bản gói không được hỗ trợ: nhận {version}, kỳ vọng {expected_version}"
        return res
        
    if not (np.isfinite(mu) and np.isfinite(s) and np.isfinite(a)):
        res["error_msg"] = "Các tham số mu, s, a không phải số thực hữu hạn"
        return res
        
    if s <= 0.0 or a <= 0.0:
        res["error_msg"] = "Tham số s hoặc a không hợp lệ (phải lớn hơn 0)"
        return res
        
    if expected_config_id is not None and config_id != expected_config_id:
        res["error_msg"] = f"Config ID không khớp: nhận {config_id}, kỳ vọng {expected_config_id}"
        return res
        
    payload_bytes = packet_bytes[18:-2]
    payload_int16 = np.frombuffer(payload_bytes, dtype='<i2')
    
    if expected_lz is not None and len(payload_int16) != expected_lz:
        res["error_msg"] = f"Số phần tử ẩn không khớp: nhận {len(payload_int16)}, kỳ vọng {expected_lz}"
        return res
        
    res["version"] = version
    res["config_id"] = config_id
    res["sequence"] = sequence
    res["mu"] = mu
    res["s"] = s
    res["a"] = a
    res["payload_int16"] = payload_int16
    res["valid"] = True
    return res

class PacketSequenceTracker:
    def __init__(self):
        self.last_sequence = None
        self.total_received = 0
        self.missing_count = 0
        self.duplicate_count = 0
        self.out_of_order_count = 0

    def feed_packet(self, packet_dict: dict) -> bool:
        if not packet_dict.get("valid", False):
            return False
        seq = packet_dict["sequence"]
        self.total_received += 1
        
        is_contiguous = True
        if self.last_sequence is not None:
            diff = seq - self.last_sequence
            if diff == 1:
                pass
            elif diff == 0:
                self.duplicate_count += 1
                is_contiguous = False
            elif diff < 0:
                self.out_of_order_count += 1
                is_contiguous = False
            else:
                self.missing_count += (diff - 1)
                is_contiguous = False
        if self.last_sequence is None or seq > self.last_sequence:
            self.last_sequence = seq
        return is_contiguous

    @staticmethod
    def can_form_context(four_packets: list, expected_config_id: int = None,
                         expected_version: int = 1) -> bool:
        """
        Kiểm tra 4 gói liên tiếp để ghép thành ngữ cảnh 32 giây:
        - Bắt buộc đủ 4 gói hợp lệ
        - Bắt buộc cùng phiên bản (mặc định version 1)
        - Bắt buộc cùng cấu hình (không ghép lẫn 8x và 16x)
        - Bắt buộc chuỗi sequence tăng liên tục không gián đoạn
        """
        if len(four_packets) != 4:
            return False
        for p in four_packets:
            if not isinstance(p, dict) or not p.get("valid", False):
                return False
                
        # Kiểm tra phiên bản
        v0 = four_packets[0].get("version", 1)
        if expected_version is not None and v0 != expected_version:
            return False
        if any(p.get("version", 1) != v0 for p in four_packets):
            return False
            
        # Kiểm tra cấu hình giống nhau trên cả 4 gói
        c0 = four_packets[0].get("config_id")
        if expected_config_id is not None and c0 != expected_config_id:
            return False
        if any(p.get("config_id") != c0 for p in four_packets):
            return False
            
        # Kiểm tra số thứ tự liên tiếp
        seqs = [p["sequence"] for p in four_packets]
        for i in range(3):
            if seqs[i + 1] != seqs[i] + 1:
                return False
        return True

def run_bit_flip_experiment(packet_bytes: bytes, n_trials: int = 10000, seed: int = 2026) -> dict:
    rng = np.random.RandomState(seed)
    total_bits = len(packet_bytes) * 8
    detected_count = 0
    
    for _ in range(n_trials):
        bit_idx = rng.randint(0, total_bits)
        byte_idx = bit_idx // 8
        bit_offset = bit_idx % 8
        
        corrupted = bytearray(packet_bytes)
        corrupted[byte_idx] ^= (1 << bit_offset)
        
        unpacked = unpack_payload(bytes(corrupted))
        if not unpacked["valid"] or not unpacked["crc_valid"]:
            detected_count += 1
            
    detection_rate = float(detected_count / n_trials * 100.0)
    return {
        "trials": n_trials,
        "detected": detected_count,
        "undetected": n_trials - detected_count,
        "detection_rate_pct": detection_rate,
        "seed": seed
    }
