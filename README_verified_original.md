# Đề tài C5: Nén tín hiệu quang thể tích có ràng buộc phổ

Đặng Gia Huy, mã số sinh viên 23110101. Giảng viên hướng dẫn: ThS. Hồ Nhựt Minh. Học kỳ I, năm học 2026–2027. Căn cứ là DangGiaHuy_DeCuong_IoT_DaChinhSua2.docx; không sửa đề cương để phù hợp kết quả.

## Phạm vi

Sáu cấu hình đã có đủ năm nếp kiểm định và mô hình cuối trên 36 người bệnh phát triển (40 bản ghi, 4200 ngữ cảnh). Mười người bệnh kiểm thử (13 bản ghi, 182 ngữ cảnh) không trùng tập phát triển nhưng tập kiểm thử đã được xem trong những lần chạy trước. Giữ lịch sử tại reports/history/ và checkpoints/legacy_run/.

Chỉ đo tham chiếu CPU máy tính và UDP cục bộ. Chưa đo vi điều khiển, ngăn xếp, vô tuyến hoặc năng lượng. Không suy diễn các đại lượng này từ tensor. MAE nhịp thở chưa đạt mốc kỳ vọng 1,5 nhịp thở/phút; không đổi ngưỡng theo tập kiểm thử.

## Kiểm tra và trình diễn, không huấn luyện lại

Mở PowerShell tại thư mục dự án. Dùng Python 3.12 và các thư viện requirements.txt. Kiểm chứng hiện tại dùng môi trường đã cài trên máy, không phải cài đặt mới hoặc bảo đảm tương đương trên mọi thiết bị.

```powershell
python -m pytest tests -q --basetemp reports/pytest_demo_temp
python -X utf8 demo_pipeline.py --level 8x
python -X utf8 demo_pipeline.py --level 16x
python -X utf8 verify_delivery.py
```

Thư mục reports phải tồn tại. Nếu thư mục tạm cũ bị khóa, dùng đường dẫn tạm mới trong dự án, không xóa hay đổi quyền thư mục Windows bị chặn.

Bản trình diễn đọc PLETH gốc, lọc nhân quả bằng trạng thái đúng, bỏ khoảng khởi động 32 giây, chuẩn hóa, chạy bộ mã hóa TorchScript đã xuất, lượng tử hóa, đóng gói và gửi UDP tới 127.0.0.1. Bên nhận kiểm tra phiên bản, cấu hình, chiều dài và CRC; hoàn nguyên bằng hệ số FLOAT32 đọc từ gói; ghép bốn khối liên tiếp để đọc nhịp thở. Giá trị không hợp lệ được ghi thiếu cùng lý do, không thay bằng 0. Kết quả ở reports/demo/. Đây không phải truyền vô tuyến.

verify_delivery.py tạo bản sao sạch mới trong reports/reproducibility/, chạy toàn bộ kiểm thử hiện tại, hai bản trình diễn CPU và tái tạo đánh giá. Mặc định dùng CUDA nếu có, tương ứng lần đánh giá gốc trên máy này; có thể chỉ định --device cpu. Không gọi huấn luyện. Ba bảng CSV được tái tạo, số tổng hợp được so sánh với dung sai tuyệt đối 0,0001; CSV làm tròn có thể lệch 0,01 tại biên làm tròn. Mã băm dữ liệu và checkpoint phải giữ nguyên. verification.json ghi sai khác thực tế và môi trường. Kiểm chứng cùng thiết bị không đồng nghĩa với tương đương từng bit giữa CPU và CUDA hoặc cài mới trên máy khác.

## Phân tích nhịp thở và dựng báo cáo

```powershell
python -X utf8 inspect_rr_features.py
python -X utf8 analyze_rr.py
python -X utf8 run_experiments.py --stage evaluate --device cpu
python -X utf8 run_experiments.py --stage report --device cpu
```

Trong phiên làm việc này, thống kê CSV và tạo tài liệu dùng Python tích hợp của ứng dụng. Môi trường độc lập cần NumPy và python-docx. Có thể đặt C5_DOCUMENT_PYTHON tới môi trường tạo tài liệu đã kiểm chứng. Microsoft Word trên Windows cập nhật mục lục và xuất PDF; phải xem mọi trang sau mỗi lần dựng. Số trang của bản dựng mới được ghi tại reports/delivery_document_qa.json sau khi xem từng trang. Các chương và phần chính bắt đầu trên trang mới; giữ lề trái 2,5 cm, các lề còn lại 2 cm, căn đều nội dung và giãn dòng 1,2.

Giai đoạn report dùng kết quả đã lưu, không huấn luyện và không đo lại CPU. Phân tích bổ sung ở reports/rr_analysis/ không được dùng để chọn tham số theo tập kiểm thử.

Chỉ khi cần đo lại CPU mới chạy:

```powershell
python -X utf8 run_experiments.py --stage benchmark --device cpu
```

Phép đo dùng 20 lượt khởi động và ít nhất 100 lượt đo cho cả hai mức. Không cộng thời gian chờ thu nhận 8/32 giây vào tính toán. Bộ nhớ tiến trình RSS là ảnh chụp, không phải đỉnh bộ nhớ nhúng. Các giai đoạn all, cross_validate và train_final có thể huấn luyện lại; không dùng để trình diễn hay kiểm tra hồ sơ.

## Quy ước và số liệu

Dải ưu tiên nhịp thở trong hàm mất mát là 0,1–0,4 Hz; dải tìm kiếm của bộ đọc là 0,1–0,7 Hz. Ngưỡng đồng thuận nhãn là 2 nhịp thở/phút, ngưỡng RIIV/RIAV là 3. Gói 8× có 250 byte và 115 hệ số; gói 16× có 124 byte và 52 hệ số, tỷ số nén thực 16,129 lần. Phần cố định có 20 byte, gồm đầu 18 byte và CRC 2 byte. Thiếu checkpoint phải báo lỗi, không xuất trọng số ngẫu nhiên.

Đánh giá dùng 182 ngữ cảnh, 728 khối và 10 người bệnh (13 bản ghi); có 172 nhãn nhịp thở hợp lệ, 10 nhãn thiếu và 48 ngữ cảnh chung của toàn bộ phương pháp (bao phủ 10/10 người bệnh). Chỉ số chính lấy trung bình đều theo người bệnh, khoảng tin cậy 95% bằng lấy mẫu lại 2.000 lần theo người bệnh; so sánh ghép cặp trên cùng tập hợp lệ.

MAE nhịp thở chung trên tập giao là 6,68 khi không nén, 6,69 ở mức 8× và 6,70 ở mức 16×. Độ bao phủ nhịp thở tương ứng 49,4%, 46,5%, 50,0%, mẫu số là 172 ngữ cảnh có nhãn hợp lệ. Dr là mức thay đổi đầu ra, không đồng nghĩa với chênh lệch MAE. results_summary.json giữ số đầy đủ; CSV đã làm tròn.

## Hồ sơ

configs/, models/, utils/, tests/ và các tệp Python chứa quy trình; data/ giữ dữ liệu và phân chia người bệnh. checkpoints/ giữ 30 mô hình kiểm định, sáu mô hình cuối, hai bộ mã hóa xuất và kết quả kiểm định. manifest.json lưu mã băm đầu vào và sản phẩm.

Báo cáo chính: Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.docx. PDF cùng tên dùng xem bố cục. results_summary.json và ba results_per_*.csv là số liệu chính. sample_packet_*.bin và tệp mô tả là bằng chứng định dạng.

Hồ sơ nộp sạch được tạo tại delivery/, kèm báo cáo, mã nguồn, dữ liệu, mô hình hợp lệ, phép thử và bằng chứng. Lịch sử được giữ ngoài hồ sơ nộp. Không xóa dữ liệu gốc, đề cương, báo cáo mẫu, checkpoint hoặc nhật ký để che các lần đã xem tập kiểm thử.

## Giới hạn truy nguyên huấn luyện

Nhật ký lần chạy trước ghi nhận việc thay hàng loạt source_code_hash trong 36 checkpoint sau huấn luyện. Không thể khôi phục mã băm huấn luyện ban đầu chỉ từ trường đã bị ghi đè. Bản sửa giữ nguyên toàn bộ byte checkpoint và ghi bằng chứng tại checkpoints/existing_run_evidence.json. Bằng chứng này chỉ cho phép tái hiện suy luận từ đúng các tệp đã cung cấp, không dùng để tiếp tục huấn luyện hoặc khẳng định đã xác minh đầy đủ nguồn gốc huấn luyện. Không đổi ngưỡng hoặc huấn luyện lại để làm đẹp kết quả kiểm thử.

Mỗi lần đánh giá và đo CPU có dấu vân tay gồm mã nguồn, dữ liệu đã xử lý, phân chia và checkpoint. Dựng báo cáo phải kiểm tra dấu vân tay; không dùng bảng cũ chỉ vì YAML giống nhau. JSON chuẩn dùng null cho số thiếu, kèm trạng thái riêng; SNRc vô hạn của tín hiệu không nén được ghi trạng thái positive_infinity_perfect_reconstruction, không biến thành 0.

Chuẩn bị môi trường theo requirements.txt; môi trường đã kiểm chứng dùng Python 3.12.7 và PyTorch 2.6.0+cu124. Muốn tái hiện đúng biến thể CUDA này cần nguồn wheel cu124 của PyTorch, không chỉ tên phiên bản torch==2.6.0. Hồ sơ không xác nhận đã cài đặt thành công trong môi trường mới.
