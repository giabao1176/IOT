# Nén tín hiệu PPG có ràng buộc phổ phục vụ ước lượng nhịp tim và tần số hô hấp

Đặng Gia Huy, mã số 23110101. Học phần Trí tuệ nhân tạo cho IoT. Giảng viên hướng dẫn Hồ Nhựt Minh.

## Cấu trúc hồ sơ

| Thư mục hoặc tệp | Nội dung |
| --- | --- |
| `run.py` | Điểm chạy chung cho các bước của bài |
| `pipeline/` | Chuẩn bị dữ liệu, huấn luyện, đánh giá, đo máy tính, xuất mô hình và kiểm chứng |
| `models/` | Kiến trúc tự mã hóa và phương pháp DCT đối chiếu |
| `utils/` | Hàm mất mát, bộ đọc sinh lý, gói nhị phân và kiểm tra tính toàn vẹn |
| `configs/` | Cấu hình theo đề cương |
| `tests/` | Kiểm thử tự động |
| `checkpoints/` | Mô hình đã lưu, mô hình xuất và bằng chứng kiểm định |
| `data/` | Phân chia người bệnh và dữ liệu; dữ liệu lớn chỉ kèm ZIP LMS |
| `evidence/` | Đề cương, kết quả và kiểm chứng trước khi sắp xếp, nhật ký thay đổi cấu trúc |
| `reports/` | Nhật ký kiểm chứng mới, không chứa bản sao mã hoặc ảnh xem trước cũ |
| `results_*.csv`, `results_summary.json` | Bảng kết quả đã đo, không dùng số liệu dự phòng |
| `sample_packet*` | Gói tin mẫu và thông tin đối chiếu |

Các công cụ tạo báo cáo cũ, tệp rà soát tài liệu, ảnh xem trước và bộ nhớ đệm không nằm trong bản mã nộp. Dự án gốc và lịch sử Git vẫn được giữ; việc dọn hồ sơ không xóa lịch sử thí nghiệm.

## Môi trường

Môi trường đã kiểm chứng: Python 3.12.7, PyTorch 2.6.0+cu124 trên Windows. Chưa xác nhận cài mới trên máy khác. Mở terminal tại thư mục `Ma_nguon_C5`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
```

Nếu chỉ dùng CPU, chọn `--device cpu` ở bước đánh giá hoặc kiểm chứng. Không cam kết kết quả từng bit giống nhau giữa CPU và CUDA.

## Kiểm tra bài không huấn luyện lại

```powershell
python run.py --help
python -m pytest tests -q --basetemp reports/pytest_temporary
python -X utf8 run.py demo --level 8x
python -X utf8 run.py demo --level 16x
python -X utf8 run.py verify
```

`verify` tạo một vùng làm việc sạch, chạy kiểm thử, đối chiếu hai bộ mã hóa xuất, trình diễn hai mức nén, rồi tái tạo bảng đánh giá từ mô hình đã lưu. Lệnh này không huấn luyện. Kết quả kiểm chứng được lưu trong `reports/reproducibility/`. Không chạy `--stage all` để kiểm tra bài nộp vì bước đó bao gồm huấn luyện.

Muốn đánh giá lại trực tiếp hoặc đo lại trên máy tính:

```powershell
python -X utf8 run.py experiments --stage evaluate --device cpu
python -X utf8 run.py experiments --stage benchmark --device cpu
```

Đánh giá và đo máy tính sẽ cập nhật các tệp kết quả. Sao lưu trước khi chạy. Thời gian đo mới phụ thuộc máy; không thay số đo trong báo cáo bằng số từ máy khác mà không giải thích.

## Dữ liệu

Bộ BIDMC lấy từ [PhysioNet](https://physionet.org/content/bidmc/1.0.0/). Giữ nguồn và giấy phép của dữ liệu. Phân chia đã khóa nằm ở `data/splits_subject.json`: 36 người bệnh phát triển và 10 người bệnh kiểm thử, không trùng người bệnh.

ZIP LMS chứa cả dữ liệu thô và tệp đã xử lý để tái hiện đúng đầu vào. GitHub không chứa dữ liệu thô hoặc tệp `.npy` khoảng 173 MiB. Nếu chỉ tải GitHub, có thể tải và xử lý dữ liệu:

```powershell
python -X utf8 run.py download
python -X utf8 run.py prepare
```

Không chạy hai lệnh này để ghi đè dữ liệu của hồ sơ đã xác minh. Dữ liệu tạo lại phải trùng các mã băm đầu vào trong `checkpoints/existing_run_evidence.json`; nếu không, hệ thống từ chối sử dụng mô hình đã lưu. Tệp nhị phân tạo lại có thể phụ thuộc phiên bản thư viện. Dùng dữ liệu từ ZIP LMS khi kiểm tra kết quả đã công bố.

## Huấn luyện cho nghiên cứu mới

Các lệnh này không cần cho việc kiểm tra bài. Sao lưu mô hình và kết quả trước khi chạy. Chỉ huấn luyện sau khi các kiểm thử đạt và đánh giá PPG không nén trên tập phát triển đã được lưu.

```powershell
python -X utf8 run.py baseline
python -X utf8 run.py experiments --stage cross_validate --device cuda
python -X utf8 run.py experiments --stage train_final --device cuda
```

Sáu cấu hình có năm nếp kiểm định riêng, tổng cộng 30 nếp. Mô hình cuối dùng trung vị số vòng tốt nhất riêng từng cấu hình. Không điều chỉnh theo tập kiểm thử.

## Kết quả và giới hạn

Đánh giá gồm 182 ngữ cảnh 32 giây và 728 khối 8 giây; có 172 nhãn nhịp thở hợp lệ. Tập giao so sánh sinh lý gồm 48 ngữ cảnh thuộc 10 người bệnh. Trung bình được tính đều theo người bệnh; khoảng tin cậy 95% dùng 2.000 lần lấy mẫu lại theo người bệnh. CSV có làm tròn, JSON lưu độ chính xác đầy đủ.

PRD của phương pháp đề xuất 8× và 16× lần lượt là 1,84% và 12,24%. MAE nhịp thở trên tập giao lần lượt là 6,69 và 6,70 nhịp/phút, chưa đạt kỳ vọng 1,5. Độ bao phủ trên 172 nhãn là 46,5% và 50,0%. Dải ưu tiên nhịp thở trong hàm mất mát là 0,1–0,4 Hz; dải tìm kiếm của bộ đọc là 0,1–0,7 Hz. Hai dải có vai trò khác nhau.

Chỉ đo tham chiếu trên máy tính và UDP cục bộ. Chưa đo vi điều khiển, vô tuyến, năng lượng, ngăn xếp hay RAM đỉnh trên phần cứng đích. Không suy diễn số đo phần cứng từ kích thước tensor hoặc bộ nhớ tiến trình.

Tập kiểm thử đã được xem trong các lần chạy trước. Trường `source_code_hash` trong các checkpoint từng bị ghi đè sau huấn luyện nên không xác minh được hoàn toàn nguồn gốc mã huấn luyện ban đầu. `existing_run_evidence.json` chứng minh byte mô hình và đầu vào phục vụ tái hiện, không chứng minh toàn bộ nguồn gốc huấn luyện.

`evidence/original_run/` giữ nguyên bằng chứng trước khi sắp xếp; các đường dẫn và dấu vân tay trong đó thuộc cấu trúc cũ. `evidence/layout_migration.json` ghi nhận đường dẫn mới và hàm thay đổi. Kiểm chứng mới có dấu vân tay riêng, không sửa mã băm checkpoint để hợp thức hóa cấu trúc mới. Bản Word và PowerPoint ở cấp trên là báo cáo hiện hành; các tên tệp mã cũ trong phụ lục Word tương ứng với tệp cùng tên trong `pipeline/`.
