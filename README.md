# Nén tín hiệu PPG có ràng buộc phổ phục vụ ước lượng nhịp tim và tần số hô hấp

Đề tài C5, học phần Trí tuệ nhân tạo cho IoT. Sinh viên Đặng Gia Huy, mã số 23110101. Giảng viên hướng dẫn Hồ Nhựt Minh. Căn cứ triển khai là `DangGiaHuy_DeCuong_IoT_DaChinhSua2.docx`.

## Hồ sơ và phạm vi

Kho GitHub chứa mã nguồn, cấu hình, kiểm thử, mô hình đã lưu, gói tin mẫu và bảng kết quả. Hồ sơ nén trên LMS kèm dữ liệu thô và tệp dữ liệu đã xử lý để tái hiện từ đúng đầu vào đã xác minh. Tệp `.npy` khoảng 173 MiB không đưa vào GitHub. Khi kiểm tra bài nộp, ưu tiên giải nén toàn bộ hồ sơ LMS rồi mở thư mục `Ma_nguon_C5`.

Không cần huấn luyện lại để kiểm tra kết quả đã báo cáo. Chỉ đo tham chiếu trên CPU máy tính và UDP cục bộ. Chưa đo vi điều khiển, truyền vô tuyến, năng lượng, ngăn xếp hoặc RAM đỉnh trên phần cứng đích. Mốc sai số trong đề cương là kỳ vọng tham khảo. Sai số nhịp thở và độ bao phủ còn hạn chế, không kết luận về hiệu quả lâm sàng.

## Chuẩn bị môi trường

Môi trường đã kiểm chứng dùng Python 3.12.7, PyTorch 2.6.0+cu124 trên Windows. Chưa xác nhận cài mới trên một máy khác. Cài các gói trong `requirements.txt`; để dùng đúng biến thể CUDA cần lấy PyTorch từ nguồn wheel cu124 thay vì chỉ chọn phiên bản chung.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
```

Nếu chỉ dùng CPU, chọn `--device cpu` cho bước đánh giá. Kết quả khác thiết bị có thể lệch nhẹ do phép toán số thực; không tuyên bố tương đương từng bit giữa CPU và CUDA.

## Dữ liệu và phân chia

Bộ dữ liệu BIDMC có tại [PhysioNet](https://physionet.org/content/bidmc/1.0.0/). Các tệp gốc, giấy phép và nguồn phải được giữ khi sử dụng. `data/splits_subject.json` lưu phân chia đã khóa theo người bệnh: 36 người bệnh phát triển, 10 người bệnh kiểm thử, không trùng người bệnh. Không tạo lại phân chia theo bản ghi.

Trong hồ sơ LMS đã có dữ liệu cần thiết. Nếu chỉ tải kho GitHub, có thể lấy dữ liệu và xử lý bằng các lệnh sau; không chạy khi đang muốn giữ nguyên tệp dữ liệu của hồ sơ đã xác minh:

```powershell
python -X utf8 download_annotations.py
python -X utf8 prepare_data.py
```

Sau khi tạo lại dữ liệu, phải đối chiếu mã băm với `manifest.json` và `checkpoints/existing_run_evidence.json`. Tệp dữ liệu không trùng mã băm sẽ bị từ chối khi tái hiện mô hình đã lưu. Việc tạo lại tệp nhị phân có thể phụ thuộc phiên bản thư viện; bản dữ liệu trong ZIP LMS là căn cứ cho kết quả đã công bố.

## Kiểm tra và trình diễn không huấn luyện

Chạy PowerShell tại thư mục chứa mã. Tạo thư mục `reports` nếu chưa có.

```powershell
python -m pytest tests -q --basetemp reports/pytest_submission_temp
python -X utf8 demo_pipeline.py --level 8x
python -X utf8 demo_pipeline.py --level 16x
python -X utf8 verify_delivery.py
```

`verify_delivery.py` tạo thư mục kiểm chứng mới, chạy kiểm thử, đối chiếu bộ mã hóa xuất và tái tạo đánh giá từ checkpoint đã lưu. Mặc định dùng CUDA nếu có; thêm `--device cpu` để chọn CPU. Lệnh này không huấn luyện. Không dùng `--stage all` để trình diễn bài nộp.

Muốn đánh giá lại trực tiếp:

```powershell
python -X utf8 run_experiments.py --stage evaluate --device cpu
```

Muốn đo lại máy tính, dùng `--stage benchmark --device cpu`; phép đo này thay đổi theo máy và phải được công bố riêng. Mô hình xuất nằm trong `checkpoints/ppg_encoder_*_traced.pt`; `export_encoder.py` cung cấp hàm xuất. Trình diễn kiểm tra gói tin thực, CRC, hệ số FLOAT32 đọc từ gói và ghép bốn khối liên tiếp để ước lượng nhịp thở. UDP dùng địa chỉ nội bộ 127.0.0.1, không phải vô tuyến.

## Huấn luyện khi chủ động thực hiện nghiên cứu mới

Các lệnh sau chỉ phục vụ nghiên cứu mới, không phải bước bắt buộc để kiểm tra bài nộp. Sao lưu mô hình và kết quả trước khi chạy vì chúng có thể thay thế sản phẩm hiện có. Chỉ huấn luyện sau khi kiểm thử và đánh giá PPG không nén trên tập phát triển đã đạt và được lưu.

```powershell
python -X utf8 evaluate_dev_uncompressed.py
python -X utf8 run_experiments.py --stage cross_validate --device cuda
python -X utf8 run_experiments.py --stage train_final --device cuda
```

Sáu cấu hình đã có năm nếp kiểm định riêng, tổng cộng 30 nếp. Mô hình cuối dùng trung vị số vòng tốt nhất của từng cấu hình. Không chỉnh tham số theo tập kiểm thử.

## Kết quả và giới hạn truy nguyên

Đánh giá chính gồm 182 ngữ cảnh 32 giây và 728 khối 8 giây, 172 nhãn RR hợp lệ. Tập giao so sánh sinh lý gồm 48 ngữ cảnh, bao phủ 10 người bệnh. Kết quả lấy trung bình đều theo người bệnh, khoảng tin cậy 95% dùng 2.000 lần lấy mẫu lại theo người bệnh. Các bảng CSV đã làm tròn; JSON giữ số đầy đủ.

PRD của đề xuất 8× và 16× lần lượt là 1,84% và 12,24%. MAE-RR trên tập giao là 6,69 và 6,70 nhịp thở/phút, chưa đạt kỳ vọng 1,5. Độ bao phủ RR trên 172 nhãn hợp lệ là 46,5% và 50,0%. Dải ưu tiên RR trong hàm mất mát là 0,1–0,4 Hz; dải tìm kiếm bộ đọc là 0,1–0,7 Hz. Hai dải có vai trò khác nhau.

Tập kiểm thử đã được xem trong các lần chạy trước. Không gọi đây là tập kiểm thử chưa từng mở. Lịch sử đầy đủ được giữ trong dự án gốc, không xóa để che hạn chế.

Nhật ký trước đây ghi nhận `source_code_hash` trong 36 checkpoint đã bị ghi đè sau huấn luyện. Không thể khôi phục nguồn gốc huấn luyện ban đầu chỉ từ trường này. Mô hình được giữ nguyên; `existing_run_evidence.json` xác nhận các byte mô hình và đầu vào phục vụ tái hiện suy luận, không chứng minh đầy đủ mã huấn luyện ban đầu. `README_verified_original.md` là bản hướng dẫn tại thời điểm kiểm chứng; README hiện tại cập nhật thông tin nộp bài, không thay đổi mã tính toán.

## Báo cáo

Báo cáo Word và PowerPoint hiện hành nằm ở cấp trên thư mục mã trong hồ sơ LMS. Các kịch bản tạo báo cáo cũ được giữ để bảo toàn mã nguồn và dấu vân tay của phép đánh giá, nhưng chưa bao gồm chỉnh sửa trang bìa và phụ lục nộp bài mới. Không chạy lại kịch bản đó để thay thế bản Word đã kiểm tra.
