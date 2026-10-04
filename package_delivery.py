"""Build a checksummed submission copy, excluding history and disposable previews."""
import hashlib
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPORT = 'Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien'
SOURCES = ['prepare_data.py', 'train.py', 'evaluate.py', 'benchmark_edge.py',
           'export_encoder.py', 'generate_report_assets.py', 'generate_final_report_v2.py',
           'evaluate_dev_uncompressed.py', 'run_experiments.py', 'update_docx_fields.py',
           'plot_waveform_samples.py', 'inspect_rr_features.py', 'analyze_rr.py',
           'demo_pipeline.py', 'verify_delivery.py', 'package_delivery.py', 'download_annotations.py',
           'report_math.py', 'document_qa.py', 'capture_existing_run.py', 'audit_saved_run.py']

def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    latest = json.loads((BASE / 'reports/reproducibility/latest.json').read_text(encoding='utf-8'))
    evidence = Path(latest['evidence'])
    verification = json.loads((evidence / 'verification.json').read_text(encoding='utf-8'))
    assert not verification['training_invoked'] and verification['input_and_checkpoint_hashes_unchanged']
    assert all(a['returncode'] == 0 for a in verification['actions'])
    qa = json.loads((BASE / 'reports/delivery_document_qa.json').read_text(encoding='utf-8'))
    assert qa['all_pages_visually_reviewed'] and qa['pages'] >= 15 and not qa['broken_anchors']
    assert qa['pages'] <= 25 or qa.get('page_limit_user_override') is True
    assert qa['docx_sha256'] == sha(BASE / (REPORT + '.docx'))
    manifest_path = BASE / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for relative, expected in verification['source_sha256'].items():
        assert sha(BASE / relative) == expected, 'Source changed after verification: ' + relative
    for relative, expected in verification['result_sha256'].items():
        assert sha(BASE / relative) == expected, 'Result changed after verification: ' + relative
    for relative, expected in verification['input_sha256'].items():
        assert sha(BASE / relative) == expected, 'Input changed after verification: ' + relative
    manifest['source_code_checksums_sha256'] = verification['source_sha256']
    assert qa['pdf_sha256'] == sha(BASE / (REPORT + '.pdf'))
    file_map = {'final_report_docx': REPORT + '.docx', 'final_report_pdf': REPORT + '.pdf',
                'requirements_txt': 'requirements.txt', 'results_summary_json': 'results_summary.json'}
    for key, relative in file_map.items(): manifest['file_checksums_sha256'][key] = sha(BASE / relative)
    added_evidence = [BASE / 'reports/delivery_document_qa.json', BASE / 'reports/reproducibility/latest.json',
                      BASE / 'reports/REPRODUCIBILITY_NOTE.md', BASE / 'reports/checkpoint_replay_audit.json']
    for folder in ['reports/demo', 'reports/rr_analysis']:
        added_evidence += [p for p in (BASE / folder).rglob('*') if p.is_file()]
    added_evidence += [p for p in evidence.iterdir() if p.is_file()]
    manifest['delivery_evidence_checksums_sha256'] = {p.relative_to(BASE).as_posix(): sha(p) for p in added_evidence}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    destination = BASE / 'delivery' / ('C5_DangGiaHuy_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    destination.mkdir(parents=True, exist_ok=False)
    def copy(source, relative=None):
        target = destination / (relative or source.relative_to(BASE))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in SOURCES + ['README.md', 'requirements.txt', 'pytest.ini', 'manifest.json', REPORT + '.docx', REPORT + '.pdf',
                           'DangGiaHuy_DeCuong_IoT_DaChinhSua2.docx', 'DangGiaHuy_NhanXet_Va_GiaiTrinh_ChinhSua.docx',
                           'Quy_dinh_De_cuong_AI_cho_IoT_HCMUTE_Fixed.docx', 'NLP_Report_Group3.docx']:
        copy(BASE / name)
    for folder in ['configs', 'models', 'utils', 'tests', 'data', 'reports/figures']:
        shutil.copytree(BASE / folder, destination / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for source in (BASE / 'checkpoints').iterdir():
        if source.is_file(): copy(source)
    for pattern in ['results_*.csv', 'results_summary.json', 'sample_packet*']:
        for source in BASE.glob(pattern): copy(source)
    for source in added_evidence: copy(source)
    guide = """# Hồ sơ nộp đề tài C5

Báo cáo chính là tệp Word HoanThien và PDF cùng tên. Đề cương, quy định và giải trình được giữ để đối chiếu. Dữ liệu gốc, dữ liệu đã xử lý, cấu hình và các mô hình hợp lệ đều nằm trong hồ sơ này.

Đọc README trước khi chạy. Trình diễn bằng python demo_pipeline.py --level 8x và --level 16x; không cần huấn luyện. python verify_delivery.py tạo bản sao sạch và tái tạo đánh giá từ mô hình đã lưu. Không dùng --stage all nếu không chủ định huấn luyện lại.

Phân tích nhịp thở nằm trong reports/rr_analysis/. Hồ sơ tái lập và nhật ký nằm trong reports/reproducibility/. Kiểm tra bố cục nằm trong reports/delivery_document_qa.json. SHA256SUMS.json lưu mã băm của toàn bộ tệp trừ chính nó.

Kiểm chứng dùng thư mục sạch trên cùng môi trường đã cài, không chứng minh cài mới trên máy khác. Chưa đo phần cứng, vô tuyến hay năng lượng; sai số nhịp thở chưa đạt mốc kỳ vọng. Không thay đổi đề cương hoặc ngưỡng để che hạn chế.

Lịch sử được bảo tồn ở thư mục dự án gốc: reports/history/, checkpoints/legacy_run/ và các nhật ký chạy trước. Không đưa lịch sử lẫn vào sản phẩm nộp, không xóa để che việc tập kiểm thử đã được xem.

Giới hạn truy nguyên: source_code_hash của checkpoint đã bị ghi đè trong lần chạy trước. Hồ sơ giữ nguyên mô hình và chứng minh tái hiện đánh giá từ đúng các byte này, không chứng minh đã khôi phục nguồn gốc huấn luyện. Xem checkpoints/existing_run_evidence.json và README.
"""
    (destination / 'HUONG_DAN_NOP.md').write_text(guide, encoding='utf-8')
    checksums = {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob('*') if p.is_file()}
    (destination / 'SHA256SUMS.json').write_text(json.dumps(checksums, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = destination.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=4) as z:
        for p in destination.rglob('*'):
            if p.is_file(): z.write(p, p.relative_to(destination).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(checksums) + 1
        for relative, expected in checksums.items():
            with z.open(relative) as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected, relative
    result = {'directory': str(destination), 'zip': str(archive), 'files_checked': len(checksums),
              'zip_sha256': sha(archive), 'zip_bytes': archive.stat().st_size}
    (BASE / 'delivery/latest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
