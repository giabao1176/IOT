# Phân tích nhịp thở sau thí nghiệm

Phân tích dùng mô hình đã lưu và bộ đọc đã khóa. Không huấn luyện lại, không đổi dải tần hoặc ngưỡng theo tập kiểm thử.

Có 48 ngữ cảnh chung của toàn bộ phương pháp. Sai khác lớn nhất giữa phép đọc lại và CSV là 0.00500 nhịp/phút, nằm trong sai số làm tròn hai chữ số.

| Người bệnh | Ngữ cảnh chung | Không nén | Nén 8× | Nén 16× |
|---|---:|---:|---:|---:|
| s04641 | 1 | 8.41 | 8.31 | 8.30 |
| s06914 | 2 | 0.91 | 0.92 | 0.54 |
| s09968 | 3 | 2.71 | 2.91 | 3.70 |
| s11342 | 6 | 5.48 | 5.48 | 5.46 |
| s17735 | 1 | 12.09 | 12.07 | 12.20 |
| s24455 | 3 | 6.60 | 6.63 | 6.44 |
| s29093 | 13 | 0.18 | 0.19 | 0.18 |
| s29125 | 9 | 10.80 | 10.80 | 10.75 |
| s29622 | 7 | 9.51 | 9.50 | 9.44 |
| s30243 | 3 | 10.12 | 10.11 | 10.03 |

Các giá trị trong bảng là MAE nhịp thở theo từng người bệnh, đơn vị nhịp/phút. Kết quả chính lấy trung bình đều theo người bệnh, không gộp mọi ngữ cảnh thành một trung bình.

De_xuat_Day_du_16x: nhận được 86/172 ngữ cảnh có nhãn, độ bao phủ 50.0%. Lý do thất bại: {"consensus_exceeded": 86, "accepted": 86}.

De_xuat_Day_du_8x: nhận được 80/172 ngữ cảnh có nhãn, độ bao phủ 46.5%. Lý do thất bại: {"consensus_exceeded": 92, "accepted": 80}.

Uncompressed: nhận được 85/172 ngữ cảnh có nhãn, độ bao phủ 49.4%. Lý do thất bại: {"consensus_exceeded": 87, "accepted": 85}.

Tín hiệu không nén cũng có sai số đáng kể và nhiều lần hai đặc trưng không đồng thuận. Vì vậy không thể quy toàn bộ sai số cho quá trình nén. Phép nén vẫn làm thay đổi kết quả; cần xem đồng thời sai số ghép cặp và độ bao phủ.

RIIV phản ánh biến thiên cường độ; RIAV phản ánh biến thiên biên độ. Sai số của từng đặc trưng trong JSON chỉ là phân tích bổ sung trên những ngữ cảnh có đặc trưng, không thay thế MAE chính và không dùng để chọn lại tham số.

Giới hạn chưa khắc phục bằng phép phân tích này: nội suy không tạo thêm thông tin sinh lý, đặc trưng hô hấp suy ra từ PPG có thể yếu, và ngưỡng đồng thuận có thể loại nhiều ngữ cảnh. Chưa có bằng chứng để xác định nguyên nhân sinh lý cụ thể. Nếu nghiên cứu tiếp cần xác minh trên tập phát triển hoặc dữ liệu mới, giữ nguyên kết quả kiểm thử đã công bố.
