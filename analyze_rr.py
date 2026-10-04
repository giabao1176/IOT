"""Read-only, post-hoc RR analysis. Does not select parameters or train models."""
import csv
import json
from collections import Counter
from pathlib import Path
import numpy as np

BASE = Path(__file__).resolve().parent

def main():
    source = json.loads((BASE / 'reports/rr_analysis/reader_diagnostics.json').read_text(encoding='utf-8'))
    with (BASE / 'results_per_context.csv').open(encoding='utf-8-sig', newline='') as f:
        contexts = list(csv.DictReader(f))
    rows = source['rows']
    methods = sorted({r['method'] for r in rows})
    patients = sorted({r['subject_id'] for r in rows})
    max_diff = 0.0
    for row in rows:
        assert int(contexts[row['ctx_idx']]['pid']) == row['pid']
        assert contexts[row['ctx_idx']][row['method'] + '_failure_reason'] == row['failure_reason']
        recorded = contexts[row['ctx_idx']][row['method'] + '_rr_est']
        assert (row['rr_est'] is None) == (recorded == '')
        if recorded:
            diff = abs(float(recorded) - row['rr_est'])
            assert diff <= .00501, (row, recorded)
            max_diff = max(max_diff, diff)
    common = {int(c['ctx_idx']) for c in contexts if c['in_rr_intersection'].lower() in ('true', '1')}
    summaries, patient_rows = {}, []
    for method in methods:
        selected = [r for r in rows if r['method'] == method]
        labelled = [r for r in selected if r['rr_ref'] is not None and r['rr_ref'] > 0]
        accepted = [r for r in labelled if r['rr_est'] is not None]
        summaries[method] = {
            'valid_labels': len(labelled), 'accepted_with_label': len(accepted),
            'coverage_pct': 100 * len(accepted) / len(labelled),
            'failure_reasons_on_labelled': dict(Counter(r['failure_reason'] or 'accepted' for r in labelled)),
            'components_mae_on_available_labelled': {
                key: float(np.mean([abs(r[key] - r['rr_ref']) for r in labelled if r.get(key) is not None]))
                for key in ('rr_riiv', 'rr_riav')},
            'disagreement_median_on_available_labelled': float(np.median([r['discrepancy'] for r in labelled if r.get('discrepancy') is not None])),
        }
    for pid in patients:
        item = {'pid': pid, 'identifier_type': 'source_subject_id'}
        for method in methods:
            selected = [r for r in rows if r['subject_id'] == pid and r['method'] == method and r['ctx_idx'] in common]
            item['n_common'] = len(selected)
            item[method + '_mae_common_bpm'] = float(np.mean([abs(r['rr_est'] - r['rr_ref']) for r in selected])) if selected else None
        patient_rows.append(item)
    result = {'scope': 'post_hoc_diagnostic_no_tuning', 'csv_estimate_rounding_tolerance_bpm': .00501,
              'max_csv_difference_bpm': max_diff, 'n_common_contexts_all_methods': len(common),
              'methods': summaries, 'patients': patient_rows,
              'interpretation': 'Component errors use available component estimates, not the common main-evaluation set. This exploratory analysis cannot establish a causal mechanism or justify tuning on test data.'}
    destination = BASE / 'reports/rr_analysis'
    (destination / 'analysis.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    lines = ['# Phân tích nhịp thở sau thí nghiệm', '',
             'Phân tích dùng mô hình đã lưu và bộ đọc đã khóa. Không huấn luyện lại, không đổi dải tần hoặc ngưỡng theo tập kiểm thử.', '',
             f'Có {len(common)} ngữ cảnh chung của toàn bộ phương pháp. Sai khác lớn nhất giữa phép đọc lại và CSV là {max_diff:.5f} nhịp/phút, nằm trong sai số làm tròn hai chữ số.', '',
             '| Người bệnh | Ngữ cảnh chung | Không nén | Nén 8× | Nén 16× |',
             '|---|---:|---:|---:|---:|']
    for p in patient_rows:
        lines.append('| ' + ' | '.join([str(p['pid']), str(p['n_common'])] + [f"{p[m + '_mae_common_bpm']:.2f}" if p[m + '_mae_common_bpm'] is not None else 'Chưa có' for m in ('Uncompressed', 'De_xuat_Day_du_8x', 'De_xuat_Day_du_16x')]) + ' |')
    lines += ['', 'Các giá trị trong bảng là MAE nhịp thở theo từng người bệnh, đơn vị nhịp/phút. Kết quả chính lấy trung bình đều theo người bệnh, không gộp mọi ngữ cảnh thành một trung bình.', '']
    for method in methods:
        s = summaries[method]
        lines += [f"{method}: nhận được {s['accepted_with_label']}/{s['valid_labels']} ngữ cảnh có nhãn, độ bao phủ {s['coverage_pct']:.1f}%. Lý do thất bại: {json.dumps(s['failure_reasons_on_labelled'], ensure_ascii=False)}.", '']
    lines += ['Tín hiệu không nén cũng có sai số đáng kể và nhiều lần hai đặc trưng không đồng thuận. Vì vậy không thể quy toàn bộ sai số cho quá trình nén. Phép nén vẫn làm thay đổi kết quả; cần xem đồng thời sai số ghép cặp và độ bao phủ.', '',
              'RIIV phản ánh biến thiên cường độ; RIAV phản ánh biến thiên biên độ. Sai số của từng đặc trưng trong JSON chỉ là phân tích bổ sung trên những ngữ cảnh có đặc trưng, không thay thế MAE chính và không dùng để chọn lại tham số.', '',
              'Giới hạn chưa khắc phục bằng phép phân tích này: nội suy không tạo thêm thông tin sinh lý, đặc trưng hô hấp suy ra từ PPG có thể yếu, và ngưỡng đồng thuận có thể loại nhiều ngữ cảnh. Chưa có bằng chứng để xác định nguyên nhân sinh lý cụ thể. Nếu nghiên cứu tiếp cần xác minh trên tập phát triển hoặc dữ liệu mới, giữ nguyên kết quả kiểm thử đã công bố.']
    (destination / 'PHAN_TICH_NHIP_THO.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
