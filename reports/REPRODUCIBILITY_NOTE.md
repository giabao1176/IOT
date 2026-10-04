# Kiểm chứng bản sửa ngày 05/10/2026

Bản sửa không huấn luyện lại và không ghi đè checkpoint. Hồ sơ trước khi sửa được giữ trong reports/history/before_repair_20261005_024745. Kiểm tra 30 checkpoint kiểm định và sáu mô hình cuối được ghi tại reports/checkpoint_replay_audit.json. Danh sách bản ghi từng nếp và trung vị số vòng được đối chiếu, đồng thời xác nhận byte mô hình không đổi.

Nhật ký do người dùng cung cấp ghi nhận việc thay hàng loạt source_code_hash trong checkpoint sau huấn luyện. Không thể suy ra mã băm gốc từ trường đã bị ghi đè. checkpoints/existing_run_evidence.json chỉ chứng minh các byte mô hình và đầu vào được bảo tồn từ thời điểm tiếp nhận; không xác minh đầy đủ mã nguồn lúc huấn luyện. Các kiểm tra tái lập sau đây không loại bỏ giới hạn này.

Bản sửa tính lại thống kê nhãn, tập phát triển và sai lệch tại điểm nối theo người bệnh nguồn. Các số đo chính được đánh giá lại từ đúng mô hình đã lưu; không đổi tham số theo tập kiểm thử. Bộ đọc, cấu hình và định nghĩa mẫu số được công bố. Thiếu số đo phần cứng, vô tuyến, ngăn xếp hoặc năng lượng tiếp tục được ghi chưa đo.

reports/reproducibility/latest.json trỏ đến lần kiểm chứng mới nhất. verification.json ghi số phép thử, sai khác số liệu, mã băm, thiết bị và giới hạn môi trường. Đây là thư mục sao chép sạch trên cùng môi trường thư viện đã cài, không phải kiểm chứng cài mới hoặc tương đương từng bit giữa CPU và CUDA.

Số trang, công thức, liên kết và mã băm bản dựng được ghi tại reports/delivery_document_qa.json. Chỉ đặt all_pages_visually_reviewed sau khi đã xem mọi ảnh trang của chính bản dựng đó. Lề trái 2,5 cm và các lề còn lại 2 cm theo quy định; nội dung căn đều, giãn dòng 1,2, Times New Roman 12, bảng nền trắng viền đen. Người dùng cho phép báo cáo dài hơn 25 trang; không ghi rằng giảng viên đã chấp thuận ngoại lệ.
