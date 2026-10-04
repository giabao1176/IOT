import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIG_DIR = os.path.join(BASE_DIR, "reports", "figures")
os.makedirs(FIG_DIR, exist_ok=True)

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['font.size'] = 8.5

def draw_pipeline_diagram():
    # Physical width matches the Word figure: labels remain readable at print size.
    fig, ax = plt.subplots(figsize=(6.2, 6.8), dpi=300)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.02, 0.98, "A  Suy luận trên máy tính và truyền UDP cục bộ", fontsize=10, weight="bold", va="top")
    rows = [
        ("PPG gốc 125 Hz\nLọc nhân quả 0,05–8 Hz\nBỏ 32 giây đầu mỗi đoạn\nKhối 8 giây gồm 1000 mẫu",
         "Kiểm tra gói nhận\nPhiên bản, cấu hình, độ dài\nCRC và số thứ tự\nKhông ghép qua gói mất"),
        ("Chuẩn hóa theo từng khối\nLưu trung bình μ và thang s\nĐầu vào [1, 1, 1000]",
         "Giải lượng tử bằng a đọc từ gói\nD1: tuyến tính Lz → 250\nD2: tích chập 1×1 → [32, 250]\nD3: tích chập chuyển vị → [16, 500]\nD4: tích chập chuyển vị → [1, 1000]\nD3/D4: nhân 7, bước 2, đệm 3, đệm ra 1\nHoàn nguyên dạng sóng bằng s và μ"),
        ("Bộ mã hóa tích chập một chiều\nE1: nhân 7, bước 2 → [16, 500]\nE2: nhân 7, bước 2 → [32, 250]\nE3: nhân 1 → [1, 250]\nTuyến tính → Lz = 115 hoặc 52",
         "Nhịp tim theo khối 8 giây\nÍt nhất 3 đỉnh hợp lệ\n60 / trung vị khoảng nhịp\nGhép 4 khối liên tiếp → 32 giây"),
        ("Lượng tử đối xứng INT16\nĐóng gói 20 byte cố định\n18 byte đầu + 2 byte CRC\nLz = 115: gói 250 byte, 8×\nLz = 52: gói 124 byte, 16,13×",
         "Nhịp thở từ RIIV và RIAV\nNội suy tuyến tính 4 Hz\nTìm phổ 0,1–0,7 Hz\nThời lượng hữu dụng ≥ 24 giây\nĐồng thuận ≤ 3 nhịp thở/phút")
    ]
    for i, texts in enumerate(rows):
        y = 0.76 - i * 0.18
        for x, text in zip((0.02, 0.53), texts):
            ax.add_patch(patches.Rectangle((x, y), 0.45, 0.15, facecolor="white", edgecolor="black", lw=1))
            ax.text(x + 0.225, y + 0.075, text, ha="center", va="center", fontsize=8.6, linespacing=1.15)
        if i < 3:
            for x in (0.245, 0.755):
                ax.annotate("", (x, y - 0.03), (x, y), arrowprops={"arrowstyle": "->", "color": "black"})
    # Send from the bottom-left block to the top-right receiver, along the gutter.
    ax.plot([0.47, 0.5, 0.5], [0.295, 0.295, 0.835], color="black", linewidth=1)
    ax.annotate("", (0.53, 0.835), (0.5, 0.835), arrowprops={"arrowstyle": "->", "color": "black"})
    ax.text(0.02, 0.16, "B  Huấn luyện ngoại tuyến trên 36 đối tượng phát triển (40 bản ghi)", fontsize=9.5, weight="bold")
    ax.text(0.02, 0.12,
            "5 nếp riêng cho mỗi cấu hình → mã hóa → lượng tử giả lập → giải mã\n"
            "Sai số từng khối chia thang s; phổ Hann bỏ DC, epsilon 10⁻⁸\n"
            "Phổ = ½ (trung bình 4 khối 8 giây + ngữ cảnh 32 giây)\n"
            "β = 0,5; HR: trọng số 2 ở 0,8–3 Hz; RR: trọng số 4 ở 0,1–0,4 Hz\n"
            "AdamW → lan truyền ngược; giảm tốc độ học và dừng sớm",
            fontsize=8.7, va="top", linespacing=1.15)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    out_path = os.path.join(FIG_DIR, "fig_pipeline.png")
    fig.savefig(out_path)
    plt.close(fig)
    print("Da luu so do:", out_path)

def draw_packet_format():
    fig, ax = plt.subplots(figsize=(6.2, 1.7), dpi=300)
    ax.axis("off")
    ax.text(0.5, 0.98, "Cấu trúc gói nhị phân 20 byte cố định và tải trọng", ha="center", va="top", fontsize=10, weight="bold")
    fields = ["Phiên bản\n1 byte", "Cấu hình\n1 byte", "Số thứ tự\n4 byte", "Trung bình μ\n4 byte", "Thang s\n4 byte", "Lượng tử a\n4 byte"]
    for i, label in enumerate(fields):
        x = 0.01 + i * 0.163
        ax.add_patch(patches.Rectangle((x, 0.47), 0.163, 0.3, facecolor="white", edgecolor="black"))
        ax.text(x + 0.0815, 0.62, label, ha="center", va="center", fontsize=8)
    ax.add_patch(patches.Rectangle((0.01, 0.04), 0.79, 0.32, facecolor="white", edgecolor="black"))
    ax.text(0.405, 0.20, "Tải trọng INT16: 2 × Lz byte\nLz 115 → 250 byte/gói; Lz 52 → 124 byte/gói", ha="center", va="center", fontsize=8.6)
    ax.add_patch(patches.Rectangle((0.80, 0.04), 0.188, 0.32, facecolor="white", edgecolor="black"))
    ax.text(0.894, 0.20, "CRC-16\n2 byte", ha="center", va="center", fontsize=8.6)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    fig.savefig(os.path.join(FIG_DIR, "fig_packet_format.png"))
    plt.close(fig)

def generate_assets():
    draw_pipeline_diagram()
    draw_packet_format()

if __name__ == "__main__":
    generate_assets()
