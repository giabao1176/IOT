import os
import sys
import shutil
import urllib.request
import time
import argparse

def setup_bidmc_raw_data(output_dir: str = None, local_source: str = None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if output_dir is None:
        raw_dir = os.path.join(script_dir, "data", "raw_bidmc")
    else:
        raw_dir = os.path.abspath(output_dir)
    os.makedirs(raw_dir, exist_ok=True)
    
    if local_source is None:
        potential_local = os.path.join(os.path.dirname(script_dir), "04-BaiTap-Holospectrum-DeBai", "data", "bidmc")
        if os.path.exists(potential_local):
            local_source = potential_local
            
    print(f"Thư mục lưu trữ dữ liệu thô: {raw_dir}")
    physionet_root = "https://physionet.org/files/bidmc/1.0.0/"
    headers = {'User-Agent': 'Mozilla/5.0'}

    print("1. Kiểm tra và sao chép/tải 53 tệp tín hiệu .dat và .hea...")
    sig_count = 0
    for i in range(1, 54):
        p_id = f"bidmc{i:02d}"
        for ext in [".dat", ".hea"]:
            fname = f"{p_id}{ext}"
            dst = os.path.join(raw_dir, fname)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                sig_count += 1
                continue
            copied = False
            if local_source and os.path.exists(os.path.join(local_source, fname)):
                src = os.path.join(local_source, fname)
                shutil.copy2(src, dst)
                sig_count += 1
                copied = True
            if not copied:
                url = physionet_root + fname
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=20) as resp, open(dst, "wb") as f_out:
                        f_out.write(resp.read())
                    sig_count += 1
                except Exception as e:
                    print(f"Cảnh báo: Không thể tải {fname} từ PhysioNet: {e}")
    print(f"-> Đã có {sig_count}/106 tệp tín hiệu thô (.dat, .hea) tại {raw_dir}")
    
    print("\n2. Kiểm tra và tải 53 bộ tệp Numerics, Breaths và Fix từ PhysioNet...")
    physionet_csv = physionet_root + "bidmc_csv/"
    downloaded_count = 0
    for i in range(1, 54):
        prefix = f"bidmc_{i:02d}"
        for suffix in ["_Numerics.csv", "_Breaths.csv", "_Fix.txt"]:
            fname = f"{prefix}{suffix}"
            dst = os.path.join(raw_dir, fname)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                downloaded_count += 1
                continue
            url = physionet_csv + fname
            success = False
            for retry in range(3):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=20) as resp, open(dst, "wb") as f_out:
                        f_out.write(resp.read())
                    success = True
                    downloaded_count += 1
                    break
                except Exception as e:
                    print(f"Lỗi tải {fname} (lần {retry+1}): {e}")
                    time.sleep(1)
            if not success:
                print(f"THẤT BẠI khi tải: {fname}")
        if i % 10 == 0 or i == 53:
            print(f"Đã kiểm tra/tải đến bệnh nhân {i:02d}/53...")
            
    print(f"\n-> Hoàn tất chuẩn bị dữ liệu thô! Tổng cộng có tại {raw_dir}")

def main():
    parser = argparse.ArgumentParser(description="Tải và đồng bộ dữ liệu BIDMC cho đề tài C5")
    parser.add_argument("--output_dir", type=str, default=None, help="Thư mục lưu trữ raw_bidmc")
    parser.add_argument("--local_source", type=str, default=None, help="Thư mục dữ liệu cục bộ nếu có")
    args = parser.parse_args()
    setup_bidmc_raw_data(output_dir=args.output_dir, local_source=args.local_source)

if __name__ == "__main__":
    main()
