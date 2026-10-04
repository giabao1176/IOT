import os
import json
import copy
import io
from zipfile import ZipFile
import numpy as np
from report_math import REGISTRY, add_equation, custom_math, clone_equation, update_formula_references
from utils.artifact_integrity import validate_stage_fingerprint
import docx
from docx.shared import Inches, Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml, OxmlElement
from docx.oxml.ns import nsdecls, qn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIG_DIR = os.path.join(BASE_DIR, "reports", "figures")
RESULTS_PATH = os.path.join(BASE_DIR, "results_summary.json")
PREPROCESS_AUDIT_PATH = os.path.join(BASE_DIR, "data", "processed", "preprocessing_audit.json")
OUTPUT_DOCX_PATH = os.path.join(BASE_DIR, "Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.docx")

def add_nlp_cover_frame(paragraph):
    """Reuse the reference's actual image and floating drawing, only on the cover."""
    from lxml import etree
    reference = os.path.join(BASE_DIR, 'NLP_Report_Group3.docx')
    with ZipFile(reference) as package:
        root = etree.fromstring(package.read('word/document.xml'))
        ns = {'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
              'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
        anchor = copy.deepcopy(root.find('.//wp:anchor', ns))
        blip = anchor.find('.//a:blip', ns)
        old_id = blip.get(qn('r:embed'))
        rels = etree.fromstring(package.read('word/_rels/document.xml.rels'))
        target = next(r.get('Target') for r in rels if r.get('Id') == old_id)
        new_id, _ = paragraph.part.get_or_add_image(io.BytesIO(package.read('word/' + target)))
        blip.set(qn('r:embed'), new_id)
        for name in ['positionH', 'positionV']:
            anchor.find('wp:' + name, ns).set('relativeFrom', 'page')
        # The exact frame artwork is retained; centre its original geometry on A4.
        doc_pr = anchor.find('wp:docPr', ns)
        doc_pr.set('id', '9000')
        doc_pr.set('name', 'Khung trang bìa từ mẫu NLP')
        doc_pr.set('descr', 'Khung trang bìa đơn sắc lấy nguyên ảnh từ báo cáo NLP')
        drawing = OxmlElement('w:drawing')
        drawing.append(anchor)
        paragraph.add_run()._r.append(drawing)

_bookmark_id_counter = 100

def set_cell_margins(cell, top=50, bottom=50, left=80, right=80):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="{top}" w:type="dxa"/><w:bottom w:w="{bottom}" w:type="dxa"/><w:left w:w="{left}" w:type="dxa"/><w:right w:w="{right}" w:type="dxa"/></w:tcMar>')
    tcPr.append(tcMar)

def set_cell_shading(cell, color_hex="F2F2F2"):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    tcPr.append(shd)

def set_cell_borders(cell, top=None, bottom=None, left=None, right=None):
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = parse_xml(f'<w:tcBorders {nsdecls("w")}/>')
    for name, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        if val is not None:
            b = parse_xml(f'<w:{name} {nsdecls("w")} w:val="{val.get("val","single")}" w:sz="{val.get("sz","4")}" w:space="0" w:color="{val.get("color","000000")}"/>')
            tcBorders.append(b)
        else:
            b = parse_xml(f'<w:{name} {nsdecls("w")} w:val="none"/>')
            tcBorders.append(b)
    tcPr.append(tcBorders)

def add_bookmark_to_paragraph(p, bookmark_name):
    global _bookmark_id_counter
    _bookmark_id_counter += 1
    bm_id = str(_bookmark_id_counter)
    bm_start = parse_xml(f'<w:bookmarkStart {nsdecls("w")} w:id="{bm_id}" w:name="{bookmark_name}"/>')
    bm_end = parse_xml(f'<w:bookmarkEnd {nsdecls("w")} w:id="{bm_id}"/>')
    p._p.insert(0, bm_start)
    p._p.append(bm_end)

def add_hyperlink_paragraph(doc, bookmark_name, bold_tag, text_desc, space_after=1.5, line_spacing=1.2):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = line_spacing
    
    hl = parse_xml(f'<w:hyperlink {nsdecls("w")} w:anchor="{bookmark_name}" w:history="1"/>')
    r1 = parse_xml(f'<w:r {nsdecls("w")}><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:b/><w:sz w:val="24"/><w:color w:val="000000"/></w:rPr><w:t xml:space="preserve">{bold_tag} </w:t></w:r>')
    hl.append(r1)
    r2 = parse_xml(f'<w:r {nsdecls("w")}><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="24"/><w:color w:val="000000"/></w:rPr><w:t>{text_desc}</w:t></w:r>')
    hl.append(r2)
    p._p.append(hl)
    return p

def add_paragraph_styled(doc, text="", bold_prefix="", italic=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=2.5, line_spacing=1.2):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = line_spacing
    
    if bold_prefix:
        r_bold = p.add_run(bold_prefix)
        r_bold.font.name = 'Times New Roman'
        r_bold.font.size = Pt(12)
        r_bold.font.bold = True
        r_bold.font.color.rgb = RGBColor(0, 0, 0)
        
    if text:
        r_text = p.add_run(text)
        r_text.font.name = 'Times New Roman'
        r_text.font.size = Pt(12)
        r_text.font.italic = italic
        r_text.font.color.rgb = RGBColor(0, 0, 0)
    return p

def add_heading_styled(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.keep_with_next = True
    
    if level == 1:
        p.paragraph_format.page_break_before = True
        try:
            p.style = doc.styles['Heading 1']
        except Exception:
            pass
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        r = p.add_run(text)
        r.font.name = 'Times New Roman'
        r.font.size = Pt(14)
        r.font.bold = True
        r.font.color.rgb = RGBColor(0, 0, 0)
    elif level == 2:
        if text in ['DANH MỤC HÌNH ẢNH', 'DANH MỤC BẢNG BIỂU'] or text.startswith('Phụ lục B:'):
            p.paragraph_format.page_break_before = True
        try:
            p.style = doc.styles['Heading 2']
        except Exception:
            pass
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(7)
        p.paragraph_format.space_after = Pt(2.5)
        r = p.add_run(text)
        r.font.name = 'Times New Roman'
        r.font.size = Pt(13)
        r.font.bold = True
        r.font.color.rgb = RGBColor(0, 0, 0)
    elif level == 3:
        try:
            p.style = doc.styles['Heading 3']
        except Exception:
            pass
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(5)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(text)
        r.font.name = 'Times New Roman'
        r.font.size = Pt(13)
        r.font.bold = True
        r.font.italic = True
        r.font.color.rgb = RGBColor(0, 0, 0)
    return p

def add_caption_styled(doc, text, bookmark_name=None):
    p = doc.add_paragraph()
    try:
        p.style = doc.styles['Caption']
    except Exception:
        pass
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(5)
    if bookmark_name:
        add_bookmark_to_paragraph(p, bookmark_name)
    r = p.add_run(text)
    r.font.name = 'Times New Roman'
    r.font.size = Pt(11)
    r.font.italic = True
    r.font.color.rgb = RGBColor(0, 0, 0)
    return p

def add_picture_styled(doc, img_path, width, alt_text, caption_text, bookmark_name=None):
    if not os.path.exists(img_path):
        print(f"Canh bao: Khong tim thay tep anh tai: {img_path}")
        return None
    shape = doc.add_picture(img_path, width=width)
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.keep_with_next = True
    try:
        docPr = shape._inline.find(qn('wp:docPr'))
        if docPr is not None:
            docPr.set('descr', alt_text)
            docPr.set('title', alt_text)
    except Exception as e:
        print(f"Canh bao gan alt text cho hinh: {e}")
    add_caption_styled(doc, caption_text, bookmark_name=bookmark_name)
    return shape

def create_table_styled(doc, headers, data, col_widths=None, bookmark_name=None):
    table = doc.add_table(rows=len(data) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    
    # Thiết lập thuộc tính lặp hàng tiêu đề trên Word và không ngắt hàng
    hdr_tr = table.rows[0]._tr.get_or_add_trPr()
    hdr_tr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
    hdr_tr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
    
    hdr_cells = table.rows[0].cells
    for i, title in enumerate(headers):
        hdr_cells[i].text = title
        set_cell_margins(hdr_cells[i], top=45, bottom=45, left=70, right=70)
        set_cell_borders(hdr_cells[i], 
                         top={"val": "single", "sz": "6", "color": "000000"},
                         bottom={"val": "single", "sz": "6", "color": "000000"},
                         left={"val": "single", "sz": "4", "color": "000000"},
                         right={"val": "single", "sz": "4", "color": "000000"})
        p = hdr_cells[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True
        if i == 0 and bookmark_name:
            add_bookmark_to_paragraph(p, bookmark_name)
        for run in p.runs:
            run.font.name = 'Times New Roman'
            run.font.size = Pt(11)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0, 0, 0)
            
    for r_idx, row in enumerate(data):
        row_tr = table.rows[r_idx + 1]._tr.get_or_add_trPr()
        row_tr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        
        row_cells = table.rows[r_idx + 1].cells
        for c_idx, val in enumerate(row):
            row_cells[c_idx].text = str(val)
            set_cell_margins(row_cells[c_idx], top=35, bottom=35, left=70, right=70)
            is_last = (r_idx == len(data) - 1)
            bot_border = {"val": "single", "sz": "6", "color": "000000"} if is_last else {"val": "single", "sz": "4", "color": "000000"}
            set_cell_borders(row_cells[c_idx],
                             top=None,
                             bottom=bot_border,
                             left={"val": "single", "sz": "4", "color": "000000"},
                             right={"val": "single", "sz": "4", "color": "000000"})
            p = row_cells[c_idx].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if c_idx > 0 else WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.name = 'Times New Roman'
                run.font.size = Pt(11)
                run.font.color.rgb = RGBColor(0, 0, 0)
                
    if col_widths is not None:
        section = doc.sections[-1]
        available_inches = (section.page_width - section.left_margin - section.right_margin) / 914400
        factor = available_inches / sum(col_widths)
        table.autofit = False
        for column, width in zip(table.columns, col_widths):
            column.width = Inches(width * factor)
        for row in table.rows:
            for c_idx, w in enumerate(col_widths):
                row.cells[c_idx].width = Inches(w * factor)
                
    return table

def add_formula_block(doc, formula_text, label=""):
    kind = "DCT" if formula_text.startswith("X") else "STE"
    return add_equation(doc, custom_math(kind), "2.1" if kind == "DCT" else "STE")

import copy

DOC_DECUONG_PATH = os.path.join(BASE_DIR, "DangGiaHuy_DeCuong_IoT_DaChinhSua2.docx")
_doc_decuong = None

def get_decuong_doc():
    global _doc_decuong
    if _doc_decuong is None and os.path.exists(DOC_DECUONG_PATH):
        try:
            _doc_decuong = docx.Document(DOC_DECUONG_PATH)
        except Exception as e:
            print(f"Canh bao nap decuong docx: {e}")
    return _doc_decuong

def add_cloned_formula(doc, paragraph_idx, fallback_text="", label=""):
    source = get_decuong_doc()
    if source is None:
        raise ValueError("Không thể đọc công thức gốc từ đề cương")
    return clone_equation(doc, source, paragraph_idx)

def add_page_number_to_section(section, is_roman=False, start_at_1=False):
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    
    sectPr = section._sectPr
    pgNumType = sectPr.find(qn('w:pgNumType'))
    if pgNumType is None:
        pgNumType = OxmlElement('w:pgNumType')
        sectPr.append(pgNumType)
        
    if is_roman:
        pgNumType.set(qn('w:fmt'), 'lowerRoman')
    else:
        pgNumType.set(qn('w:fmt'), 'decimal')
        if start_at_1:
            pgNumType.set(qn('w:start'), '1')
            
    pPr = p._p.get_or_add_pPr()
    rPr = OxmlElement('w:rPr')
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), 'Times New Roman')
    rFonts.set(qn('w:hAnsi'), 'Times New Roman')
    sz = OxmlElement('w:sz')
    sz.set(qn('w:val'), '24') # 12 pt
    rPr.append(rFonts)
    rPr.append(sz)
    
    run_fld = OxmlElement('w:r')
    run_fld.append(rPr)
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    instrText = OxmlElement('w:instrText')
    instrText.set(qn('xml:space'), 'preserve')
    instrText.text = "PAGE"
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'separate')
    fldChar3 = OxmlElement('w:fldChar')
    fldChar3.set(qn('w:fldCharType'), 'end')
    
    run_fld.append(fldChar1)
    run_fld.append(instrText)
    run_fld.append(fldChar2)
    run_fld.append(fldChar3)
    p._p.append(run_fld)

def add_word_toc_field(doc):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(4)
    fldSimple = OxmlElement('w:fldSimple')
    fldSimple.set(qn('w:instr'), 'TOC \\o "1-3" \\h \\z \\u')
    p._p.append(fldSimple)

def generate_report():
    print("Bat dau tao bao cao tieu luan cuoi khoa C5 hoan thien tu du lieu do dac moi...")
    
    if not os.path.exists(RESULTS_PATH):
        raise FileNotFoundError(f"Khong tim thay tep ket qua: {RESULTS_PATH}")
        
    with open(RESULTS_PATH, 'r', encoding='utf-8') as f:
        res = json.load(f)
    REGISTRY.clear()
    for stage in ["evaluation", "benchmark"]:
        validate_stage_fingerprint(BASE_DIR, res["stage_fingerprints"][stage])
    stage_hashes = res.get("stage_protocol_hashes", {})
    if not res.get("protocol_hash") or any(
        stage_hashes.get(stage) != res["protocol_hash"]
        for stage in ("train_final", "evaluation", "benchmark")
    ):
        raise ValueError("Cac giai doan huan luyen, danh gia va benchmark chua cung giao thuc")
    if not os.path.exists(PREPROCESS_AUDIT_PATH):
        raise FileNotFoundError(f"Khong tim thay tep thong ke tien xu ly: {PREPROCESS_AUDIT_PATH}")
    with open(PREPROCESS_AUDIT_PATH, 'r', encoding='utf-8') as f:
        prep_audit = json.load(f)

    splits_path = os.path.join(BASE_DIR, "data", "splits_subject.json")
    if not os.path.exists(splits_path):
        raise FileNotFoundError(f"Khong tim thay tep chia tap: {splits_path}")
    with open(splits_path, 'r', encoding='utf-8') as f:
        splits_info = json.load(f)
    n_dev_subs = splits_info["dev_subjects_count"]
    n_dev_recs = splits_info["dev_records_count"]
    n_test_subs = splits_info["test_subjects_count"]
    n_test_recs = splits_info["test_records_count"]
        
    # Kiem tra tinh day du cua ket qua (khong cho phep fallback im lang)
    if "cross_validation" not in res:
        raise ValueError("Thieu thong tin cross_validation trong results_summary.json!")
    if "evaluation" not in res or "methods" not in res["evaluation"]:
        raise ValueError("Thieu thong tin evaluation.methods trong results_summary.json!")
    if "benchmark" not in res:
        raise ValueError("Thieu thong tin benchmark trong results_summary.json!")
    cv_per_config = res.get("cross_validation_per_config", {})
    if len(cv_per_config) != 6:
        raise ValueError("Can ket qua kiem dinh cheo day du cho 6 cau hinh!")
        
    cv_info = cv_per_config["De xuat Day du (8x)"]
    eval_m = res["evaluation"]["methods"]
    required = ["Uncompressed", "DCT-1D (8x)", "DCT-1D (16x)", "Autoencoder-MSE (8x)", "Autoencoder-MSE (16x)", "De xuat Day du (8x)", "De xuat Day du (16x)", "Boc tach B: Pho deu (8x)", "Boc tach C: Uu tien HR (8x)"]
    if set(required) != set(eval_m):
        raise ValueError("Báo cáo cần đủ chín phương pháp của lần đánh giá đã xác minh")
    inter_info = res["evaluation"].get("intersection_info", {})
    bench = res["benchmark"]
    n_test_ctx = inter_info["total_test_contexts"]
    n_test_blocks = inter_info["total_test_blocks"]
    
    # Trich xuat so lieu thuc te do dac
    p8 = eval_m["De xuat Day du (8x)"]
    p16 = eval_m["De xuat Day du (16x)"]
    uncomp = eval_m["Uncompressed"]
    
    p8_prdc = p8["prdc_mean"]
    p8_prdc_ci = p8["prdc_ci"]
    p8_snrc = p8["snrc_mean"]
    p8_hr_inter = p8["intersection"]["mae_hr_mean"]
    p8_hr_inter_ci = p8["intersection"]["mae_hr_ci"]
    p8_rr_inter = p8["intersection"]["mae_rr_mean"]
    p8_rr_inter_ci = p8["intersection"]["mae_rr_ci"]
    p8_rr_indiv = p8["individual"]["mae_rr_mean"]
    p8_rr_indiv_ci = p8["individual"]["mae_rr_ci"]
    p8_cov = p8["individual"]["rr_coverage_pct"]
    
    p16_prdc = p16["prdc_mean"]
    p16_prdc_ci = p16["prdc_ci"]
    p16_snrc = p16["snrc_mean"]
    p16_hr_inter = p16["intersection"]["mae_hr_mean"]
    p16_hr_inter_ci = p16["intersection"]["mae_hr_ci"]
    p16_rr_inter = p16["intersection"]["mae_rr_mean"]
    p16_rr_inter_ci = p16["intersection"]["mae_rr_ci"]
    p16_rr_indiv = p16["individual"]["mae_rr_mean"]
    p16_rr_indiv_ci = p16["individual"]["mae_rr_ci"]
    p16_cov = p16["individual"]["rr_coverage_pct"]
    p16_delta_hr = p16["delta_mae_hr"]
    p16_delta_hr_ci = p16["delta_mae_hr_ci"]
    hr_difference_text = (
        "Khoảng tin cậy của chênh lệch chứa 0, nên chưa đủ bằng chứng về khác biệt thống kê."
        if p16_delta_hr_ci[0] <= 0 <= p16_delta_hr_ci[1]
        else "Khoảng tin cậy của chênh lệch không chứa 0, cho thấy khác biệt thống kê trong phạm vi phép đánh giá này."
    )
    
    uncomp_hr_inter = uncomp["intersection"]["mae_hr_mean"]
    uncomp_rr_inter = uncomp["intersection"]["mae_rr_mean"]
    uncomp_rr_indiv = uncomp["individual"]["mae_rr_mean"]
    uncomp_cov = uncomp["individual"]["rr_coverage_pct"]

    METHOD_DISPLAY_NAMES = {
        "Uncompressed": "Chưa nén (Gốc)",
        "DCT-1D (8x)": "DCT-1D (8x)",
        "DCT-1D (16x)": "DCT-1D (16x)",
        "Autoencoder-MSE (8x)": "Mạng tự mã hóa MSE (8x)",
        "Autoencoder-MSE (16x)": "Mạng tự mã hóa MSE (16x)",
        "De xuat Day du (8x)": "Đề xuất đầy đủ (8x)",
        "De xuat Day du (16x)": "Đề xuất đầy đủ (16x)",
        "Boc tach B: Pho deu (8x)": "Bóc tách B: Phổ đều (8x)",
        "Boc tach C: Uu tien HR (8x)": "Bóc tách C: Ưu tiên nhịp tim (8x)"
    }
    
    doc = docx.Document()
    doc.styles['Normal'].font.name = 'Times New Roman'
    doc.styles['Normal'].font.size = Pt(12)
    doc.styles['Normal'].paragraph_format.line_spacing = 1.2
    
    # -------------------------------------------------------------------------
    # SECTION 0: TRANG BÌA CHÍNH (KHÔNG CÓ HEADER/FOOTER)
    # -------------------------------------------------------------------------
    sec0 = doc.sections[0]
    sec0.top_margin = Cm(2)
    sec0.bottom_margin = Cm(2)
    sec0.left_margin = Cm(2.5)
    sec0.right_margin = Cm(2)
    sec0.page_width = Cm(21)
    sec0.page_height = Cm(29.7)
    sec0.different_first_page_header_footer = True
    
    # Tiêu đề trường
    p = doc.add_paragraph()
    add_nlp_cover_frame(p)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    p.paragraph_format.space_after = Pt(45)
    r = p.add_run("BỘ GIÁO DỤC VÀ ĐÀO TẠO\nTRƯỜNG ĐẠI HỌC CÔNG NGHỆ KỸ THUẬT TP.HCM\nKHOA CÔNG NGHỆ THÔNG TIN\nNGÀNH CÔNG NGHỆ THÔNG TIN")
    r.font.name = 'Times New Roman'
    r.font.size = Pt(12)
    r.font.bold = True
    r.font.color.rgb = RGBColor(0, 0, 0)
    
    p_title_sub = doc.add_paragraph()
    p_title_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title_sub.paragraph_format.space_after = Pt(12)
    r_ts = p_title_sub.add_run("BÁO CÁO TIỂU LUẬN CUỐI KỲ\nHỌC PHẦN: TRÍ TUỆ NHÂN TẠO CHO IOT")
    r_ts.font.name = 'Times New Roman'
    r_ts.font.size = Pt(14)
    r_ts.font.bold = True
    r_ts.font.color.rgb = RGBColor(0, 0, 0)
    
    p_topic = doc.add_paragraph()
    p_topic.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_topic.paragraph_format.space_after = Pt(65)
    r_top = p_topic.add_run("ĐỀ TÀI C5:\nNÉN TÍN HIỆU QUANG THỂ TÍCH CÓ RÀNG BUỘC PHỔ PHỤC VỤ ƯỚC LƯỢNG NHỊP TIM VÀ TẦN SỐ HÔ HẤP TRÊN THIẾT BỊ ĐEO TAY INTERNET VẠN VẬT")
    r_top.font.name = 'Times New Roman'
    r_top.font.size = Pt(16)
    r_top.font.bold = True
    r_top.font.color.rgb = RGBColor(0, 0, 0)
    
    info_table = doc.add_table(rows=5, cols=2)
    info_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    info_table.rows[0].cells[0].width = Inches(2.6)
    info_table.rows[0].cells[1].width = Inches(3.8)
    
    infos = [
        ("Giảng viên hướng dẫn:", "ThS. Hồ Nhựt Minh"),
        ("Mã lớp học phần:", "AIOT331185_01CLC"),
        ("Sinh viên thực hiện:", "Đặng Gia Huy"),
        ("Mã số sinh viên:", "23110101"),
        ("Học kỳ / Năm học:", "Học kỳ I - Năm học 2026 – 2027")
    ]
    for idx, (label, val) in enumerate(infos):
        c0, c1 = info_table.rows[idx].cells[0], info_table.rows[idx].cells[1]
        c0.text = label
        c1.text = val
        for c in (c0, c1):
            set_cell_borders(c)
            set_cell_margins(c, top=35, bottom=35, left=40, right=40)
            p_cell = c.paragraphs[0]
            p_cell.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for r_run in p_cell.runs:
                r_run.font.name = 'Times New Roman'
                r_run.font.size = Pt(12)
                r_run.font.color.rgb = RGBColor(0, 0, 0)
        c0.paragraphs[0].runs[0].font.bold = True
        
    p_bot = doc.add_paragraph()
    p_bot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_bot.paragraph_format.space_before = Pt(70)
    r_bot = p_bot.add_run("THÀNH PHỐ HỒ CHÍ MINH, THÁNG 10 NĂM 2026")
    r_bot.font.name = 'Times New Roman'
    r_bot.font.size = Pt(12)
    r_bot.font.bold = True
    r_bot.font.color.rgb = RGBColor(0, 0, 0)
    
    # -------------------------------------------------------------------------
    # SECTION 1: PHẦN MỞ ĐẦU (ĐÁNH SỐ TRANG LA MÃ: i, ii, iii...)
    # -------------------------------------------------------------------------
    sec1 = doc.add_section(docx.enum.section.WD_SECTION.NEW_PAGE)
    sec1.top_margin = Cm(2)
    sec1.bottom_margin = Cm(2)
    sec1.left_margin = Cm(2.5)
    sec1.right_margin = Cm(2)
    add_page_number_to_section(sec1, is_roman=True, start_at_1=True)
    
    # Trang 1 của Sec1: Thông tin sinh viên & Bảng đánh giá của Giảng viên
    add_heading_styled(doc, "THÔNG TIN SINH VIÊN VÀ ĐÁNH GIÁ CỦA GIẢNG VIÊN", level=1)
    
    member_headers = ["Họ và tên sinh viên", "Mã số sinh viên", "Lớp sinh hoạt", "Tỷ lệ đóng góp"]
    member_data = [["Đặng Gia Huy", "23110101", "231101A", "100%"]]
    create_table_styled(doc, member_headers, member_data, col_widths=[2.4, 1.4, 1.4, 1.4])
    
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    add_paragraph_styled(doc, bold_prefix="Nhiệm vụ đảm nhiệm: ", 
                         text="Toàn bộ quy trình nghiên cứu, khảo sát dữ liệu chuẩn BIDMC, thiết kế kiến trúc mạng tích chập tự mã hóa một chiều, xây dựng hàm mất mát đa thang phổ thời gian và tần số, hiện thực hóa định dạng gói tin nhị phân và kiểm tra mã CRC-16, huấn luyện kiểm định chéo và biên soạn báo cáo tiểu luận.")
    
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    add_heading_styled(doc, "BẢNG ĐÁNH GIÁ VÀ NHẬN XÉT CỦA GIẢNG VIÊN HƯỚNG DẪN", level=2)
    
    eval_headers = ["Tiêu chí đánh giá", "Trọng số", "Điểm tối đa", "Điểm đánh giá"]
    eval_data = [
        ["1. Ý thức học tập, tính kỷ luật và tiến độ thực hiện đề tài", "10%", "1.0", ""],
        ["2. Khảo sát tài liệu, cơ sở lý thuyết và đề cương phương pháp", "20%", "2.0", ""],
        ["3. Thiết kế kiến trúc kỹ thuật, tính sáng tạo và đúng đắn của giải thuật", "25%", "2.5", ""],
        ["4. Mức độ hoàn thiện mã nguồn, tính minh bạch và độ tin cậy thực nghiệm", "25%", "2.5", ""],
        ["5. Chất lượng báo cáo tiểu luận, văn phong khoa học và hình thức trình bày", "20%", "2.0", ""],
        ["TỔNG CỘNG ĐIỂM ĐÁNH GIÁ TOÀN DIỆN", "100%", "10.0", ""]
    ]
    create_table_styled(doc, eval_headers, eval_data, col_widths=[3.8, 1.0, 1.0, 1.0])
    
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    add_paragraph_styled(doc, bold_prefix="Nhận xét của giảng viên hướng dẫn:")
    for _ in range(2):
        add_paragraph_styled(doc, "." * 100)
        
    p_sig = doc.add_paragraph()
    p_sig.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_sig.paragraph_format.space_before = Pt(6)
    p_sig.paragraph_format.space_after = Pt(2)
    r_sig = p_sig.add_run("Thành phố Hồ Chí Minh, ngày ...... tháng ...... năm 2026\nGiảng viên hướng dẫn: ThS. Hồ Nhựt Minh\n(Ký và ghi rõ họ tên)")
    r_sig.font.name = 'Times New Roman'
    r_sig.font.size = Pt(11)
    
    
    # Lời cảm ơn & Tóm tắt đề tài
    add_heading_styled(doc, "LỜI CẢM ƠN", level=1)
    add_paragraph_styled(doc, "Em xin cảm ơn ThS. Hồ Nhựt Minh, giảng viên học phần Trí tuệ nhân tạo cho IoT, đã hướng dẫn và góp ý cho đề tài.", space_after=2)
    add_paragraph_styled(doc, "Em xin cảm ơn quý thầy cô Khoa Công nghệ Thông tin, Trường Đại học Công nghệ Kỹ thuật TP.HCM, đã cung cấp kiến thức nền tảng phục vụ việc thực hiện đề tài.", space_after=3)
    
    add_heading_styled(doc, "TÓM TẮT ĐỀ TÀI", level=1)
    add_paragraph_styled(doc, "Trong các hệ thống giám sát sức khỏe từ xa thuộc Internet vạn vật y tế, truyền liên tục tín hiệu quang thể tích dạng thô làm tăng lưu lượng vô tuyến và có thể làm tăng năng lượng tiêu thụ. Nghiên cứu này hiện thực hóa giải pháp nén tín hiệu tại thiết bị biên bằng mạng tích chập tự mã hóa một chiều, phép lượng tử hóa 16 bit có xấp xỉ gradient truyền thẳng và hàm mất mát phổ đa thang.", space_after=2)
    
    add_paragraph_styled(doc, f"Thực nghiệm sử dụng 53 bản ghi BIDMC từ 46 đối tượng nguồn MIMIC II, gồm {n_dev_subs} đối tượng phát triển ({n_dev_recs} bản ghi) và {n_test_subs} đối tượng kiểm thử độc lập ({n_test_recs} bản ghi). Kiểm định chéo năm nếp được thực hiện riêng cho từng cấu hình theo nhóm đối tượng GroupKFold. Đánh giá chính dùng {n_test_ctx} ngữ cảnh 32 giây không chồng lấp, tương ứng {n_test_blocks} khối 8 giây. Ở mức nén 8 lần, PRDc là {p8_prdc:.2f}%, MAE-HR là {p8_hr_inter:.2f} nhịp/phút và MAE-RR là {p8_rr_inter:.2f} nhịp thở/phút trên tập giao hợp lệ chung. Ở mức nén 16,1 lần, các giá trị tương ứng là {p16_prdc:.2f}%, {p16_hr_inter:.2f} nhịp/phút và {p16_rr_inter:.2f} nhịp thở/phút. {hr_difference_text}", space_after=2)
    add_paragraph_styled(doc, f"Hệ thống tạo gói nhị phân có 20 byte cố định kể cả CRC-16, với dung lượng 250 byte ở mức 8 lần và 124 byte ở mức 16,1 lần. Độ bao phủ RR của hai cấu hình lần lượt là {p8_cov:.1f}% và {p16_cov:.1f}%, cần được xem cùng sai số để đánh giá mức sử dụng thực tế. Các mốc PRD 6% và 9% được giữ làm kỳ vọng tham khảo; PRDc được báo cáo riêng vì có mẫu số khác. Phần triển khai hiện được đo tham chiếu trên máy tính và chưa có số đo tài nguyên hoặc năng lượng của vi điều khiển.", space_after=3)
    
    add_paragraph_styled(doc, bold_prefix="Từ khóa: ", text="Quang thể tích; Nén tín hiệu; Mạng nơ-ron tích chập; Ràng buộc phổ tần số; Nhịp tim; Tần số hô hấp; Khung truyền nhị phân; CRC-16.", space_after=0)
    
    
    # Mục lục tự động Word
    add_heading_styled(doc, "MỤC LỤC", level=1)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    add_word_toc_field(doc)
    
    
    # Danh mục từ viết tắt
    add_heading_styled(doc, "DANH MỤC CÁC TỪ VIẾT TẮT VÀ THUẬT NGỮ", level=1)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    
    abbr_headers = ["Ký hiệu", "Thuật ngữ tiếng Anh", "Ý nghĩa giải thích trong tiếng Việt"]
    abbr_data = [
        ["PPG", "Photoplethysmogram", "Tín hiệu quang thể tích đo biến thiên thể tích máu ngoại vi"],
        ["HR", "Heart Rate", "Nhịp tim, số lần tim co bóp trong một phút (nhịp/phút)"],
        ["RR", "Respiration Rate", "Tần số hô hấp, số lần thở trong một phút (nhịp thở/phút)"],
        ["CR", "Compression Ratio", "Tỷ số nén giữa kích thước dữ liệu thô và dữ liệu sau nén"],
        ["DCT", "Discrete Cosine Transform", "Biến đổi cosin rời rạc dùng làm phương pháp đối chứng"],
        ["PRDc", "Normalized Percent Residual Difference", "Độ méo phần trăm dư chuẩn hóa sau khi trừ giá trị trung bình"],
        ["SNRc", "Reconstructed Signal-to-Noise Ratio", "Tỷ số tín hiệu trên nhiễu của tín hiệu sau giải nén (dB)"],
        ["SOS", "Second-Order Sections", "Cấu trúc bậc hai nối tiếp của bộ lọc số nhân quả"],
        ["STE", "Straight-Through Estimator", "Cơ chế ước lượng gradient thẳng phục vụ lượng tử hóa số nguyên"],
        ["CRC", "Cyclic Redundancy Check", "Mã kiểm tra phần dư tuần hoàn 16-bit phát hiện lỗi gói tin"]
    ]
    create_table_styled(doc, abbr_headers, abbr_data, col_widths=[1.1, 2.2, 3.1])
    
    doc.add_paragraph().paragraph_format.space_after = Pt(6)
    add_heading_styled(doc, "DANH MỤC HÌNH ẢNH", level=2)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    
    # Danh mục hình ảnh có liên kết anchor
    figs_metadata = [
        ("BM_FIG_3_1", "Hình 3.1:", "Sơ đồ kiến trúc tổng thể hệ thống nén biên và giải mã máy chủ"),
        ("BM_FIG_3_2", "Hình 3.2:", "Cấu trúc khung truyền gói tin nhị phân chuẩn thiết bị nhúng"),
        ("BM_FIG_4_1", "Hình 4.1:", "So sánh độ méo PRDc và sai số nhịp thở MAE giữa các mô hình ở tỷ lệ nén 8 lần"),
        ("BM_FIG_4_2", "Hình 4.2:", "Dạng sóng quang thể tích tái tạo so với dạng sóng gốc chưa nén"),
        ("BM_FIG_4_3", "Hình 4.3:", "Mật độ phổ công suất tín hiệu gốc và tín hiệu tái tạo sau giải nén"),
        ("BM_FIG_4_4", "Hình 4.4:", "Phân rã thời gian thực thi tham chiếu trên CPU máy tính cho một khối 8 giây")
    ]
    for bm_name, tag, desc in figs_metadata:
        add_hyperlink_paragraph(doc, bm_name, tag, desc, space_after=1.2, line_spacing=1.15)
        
    doc.add_paragraph().paragraph_format.space_after = Pt(6)
    add_heading_styled(doc, "DANH MỤC BẢNG BIỂU", level=2)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    
    # Danh mục bảng biểu có liên kết anchor
    tbls_metadata = [
        ("BM_TBL_3_1", "Bảng 3.1:", "Thông số kiến trúc chi tiết của mạng nơ-ron tích chập tự mã hóa"),
        ("BM_TBL_3_2", "Bảng 3.2:", "Cấu trúc trường dữ liệu chi tiết của khung truyền nhị phân cố định"),
        ("BM_TBL_4_1", "Bảng 4.1:", "Kết quả kiểm định chéo năm nếp trên tập phát triển"),
        ("BM_TBL_4_2A", "Bảng 4.2a:", "Kết quả đánh giá chất lượng phục hồi dạng sóng quang thể tích trên tập kiểm thử"),
        ("BM_TBL_4_2B", "Bảng 4.2b:", "Kết quả ước lượng các chỉ số sinh lý trên tập giao hợp lệ giữa các phương pháp"),
        ("BM_TBL_4_2C", "Bảng 4.2c:", "Thống kê chi tiết mẫu số đánh giá tập giao và lý do loại bỏ tín hiệu"),
        ("BM_TBL_4_3", "Bảng 4.3:", "Kết quả phân tích bóc tách các thành phần mất mát phổ"),
        ("BM_TBL_4_4", "Bảng 4.4:", "Kết quả kiểm thử tính bền vững trước lỗi truyền thông gói tin bằng mã CRC-16"),
        ("BM_TBL_4_5", "Bảng 4.5:", "Tài nguyên tính toán và độ trễ đo đạc tham chiếu trên môi trường CPU máy tính"),
        ("BM_TBL_4_6", "Bảng 4.6:", "Phân tích sai lệch biên độ và độ dốc tại ba điểm nối khối trong ngữ cảnh 32 giây"),
        ("BM_TBL_5_1", "Bảng 5.1:", "Bảng đối chiếu giữa mục tiêu ban đầu và kết quả thực nghiệm đo đạc thực tế")
    ]
    for bm_name, tag, desc in tbls_metadata:
        add_hyperlink_paragraph(doc, bm_name, tag, desc, space_after=1.2, line_spacing=1.15)

    # -------------------------------------------------------------------------
    # SECTION 2: TOÀN BỘ NỘI DUNG CHÍNH (ĐÁNH SỐ TRANG SỐ Ả RẬP: 1, 2, 3...)
    # -------------------------------------------------------------------------
    sec2 = doc.add_section(docx.enum.section.WD_SECTION.NEW_PAGE)
    sec2.top_margin = Cm(2)
    sec2.bottom_margin = Cm(2)
    sec2.left_margin = Cm(2.5)
    sec2.right_margin = Cm(2)
    add_page_number_to_section(sec2, is_roman=False, start_at_1=True)
    
    # -------------------------------------------------------------------------
    # CHƯƠNG 1
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "CHƯƠNG 1. TỔNG QUAN VÀ ĐẶT VẤN ĐỀ", level=1)
    
    add_heading_styled(doc, "1.1. Bối cảnh và tính cấp thiết của đề tài", level=2)
    add_paragraph_styled(doc, "Đề tài nghiên cứu nén tín hiệu quang thể tích để giảm lượng dữ liệu truyền, đồng thời đánh giá khả năng giữ thông tin nhịp tim và nhịp thở. Đây là thực nghiệm xử lý tín hiệu trên BIDMC, không phải hệ thống chẩn đoán lâm sàng.")
    add_paragraph_styled(doc, "Trong bộ dữ liệu BIDMC, tín hiệu được lấy mẫu ở 125 Hz. Nếu truyền trực tiếp, mỗi khối 8 giây gồm 1.000 mẫu số nguyên 16 bit, tương ứng 2.000 byte ở tầng ứng dụng. Nén dữ liệu tại nút cảm biến trước khi truyền giúp giảm số byte của tải ứng dụng. Tuy nhiên, nghiên cứu chưa đo điện năng trên thiết bị thật nên không suy luận mức tiết kiệm pin từ mức giảm dung lượng gói.")
    
    add_heading_styled(doc, "1.2. Thách thức kỹ thuật trong bài toán nén tín hiệu quang thể tích", level=2)
    add_paragraph_styled(doc, "Khác với tín hiệu thoại hoặc hình ảnh, tín hiệu quang thể tích chứa đựng đồng thời hai dấu hiệu sinh tồn cốt lõi ở hai thang tần số rất khác biệt:")
    add_paragraph_styled(doc, "1. Nhịp tim phản ánh qua các đỉnh co bóp tâm thu biên độ lớn trong dải tần số 0.8 đến 3.0 Hz. Thành phần này tương đối dễ bảo tồn nếu mô hình nén giữ được hình thái sóng.")
    add_paragraph_styled(doc, "2. Đề cương ưu tiên dải 0,1–0,4 Hz trong hàm mất mát phổ 32 giây. Đây là dải trọng số khi huấn luyện, khác với dải tìm kiếm 0,1–0,7 Hz của bộ đọc nhịp thở.")
    add_paragraph_styled(doc, "3. Thách thức triển khai trên thiết bị biên, với ngân sách mục tiêu 256 KiB cho trọng số bộ mã hóa, RAM đỉnh mục tiêu 2 MiB và thời gian mã hóa mục tiêu 20 mili giây cho mỗi khối. Các mốc này chỉ được kết luận sau khi đo trên phần cứng cụ thể.")
    
    add_heading_styled(doc, "1.3. Mục tiêu và phạm vi nghiên cứu của đề tài", level=2)
    add_paragraph_styled(doc, "Đề tài tập trung giải quyết bài toán nén tín hiệu quang thể tích tại thiết bị biên với các mục tiêu cụ thể:")
    add_paragraph_styled(doc, "1. Xây dựng quy trình xử lý tín hiệu số nhân quả, chia khối 8 giây độc lập, chuẩn hóa Z-score cục bộ và đóng gói theo khung truyền nhị phân cố định 18 byte trước CRC.")
    add_paragraph_styled(doc, "2. Thiết kế kiến trúc mạng tích chập tự mã hóa một chiều kích thước gọn nhẹ, tích hợp lượng tử hóa nhận biết huấn luyện về số nguyên có dấu 16-bit phục vụ hai tỷ lệ nén 8 lần và 16.1 lần.")
    add_paragraph_styled(doc, "3. Đề xuất hàm mất mát đa thang phổ thời gian và tần số có trọng số nhằm ràng buộc đồng thời dải nhịp tim trên từng khối 8 giây và dải nhịp thở trên ngữ cảnh 32 giây sau khi ghép nối hoàn nguyên.")
    add_paragraph_styled(doc, "4. Đánh giá kiểm định chéo và kiểm thử khóa độc lập trên bộ dữ liệu BIDMC gồm 53 bản ghi của 46 người bệnh nguồn, phân tích khách quan các chỉ số đạt được và các giới hạn còn tồn đọng.")
    
    add_heading_styled(doc, "1.4. Đóng góp khoa học và kỹ thuật chính", level=2)
    add_paragraph_styled(doc, "Sản phẩm gồm quy trình nén và giải mã qua gói nhị phân, bộ kiểm thử, mô hình xuất và kết quả theo cửa sổ, ngữ cảnh, người bệnh. So sánh dùng cùng tập mẫu hợp lệ và công bố độ bao phủ để làm rõ giới hạn của bộ đọc sinh lý.")
    
    # -------------------------------------------------------------------------
    # CHƯƠNG 2
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "CHƯƠNG 2. CƠ SỞ LÝ THUYẾT VÀ NGUYÊN LÝ HOẠT ĐỘNG", level=1)
    
    add_heading_styled(doc, "2.1. Bản chất sinh lý của tín hiệu quang thể tích", level=2)
    add_paragraph_styled(doc, "Tín hiệu quang thể tích được thu nhận bằng phương pháp quang học không xâm lấn. Nguồn sáng LED chiếu qua bề mặt da và một đi-ốt quang ghi nhận lượng ánh sáng phản xạ hoặc truyền qua. Sự thay đổi hấp thụ và tán xạ ánh sáng theo nhịp mạch tạo nên thành phần dao động của tín hiệu. Mối liên hệ còn phụ thuộc bước sóng, vị trí đo và đặc tính mô, nên không thể diễn giải thành một quan hệ tỷ lệ nghịch đơn giản với thể tích máu [5, 7].")
    add_paragraph_styled(doc, "Tín hiệu quang thể tích chuẩn y tế bao gồm thành phần một chiều phản ánh sự hấp thụ không đổi của mô, xương và máu tĩnh mạch, cùng thành phần xoay chiều dao động theo từng nhịp co bóp tim. Khoảng cách giữa các đỉnh tâm thu phản ánh khoảng thời gian giữa các nhịp tim, là cơ sở để trích xuất chỉ số nhịp tim tức thời.")
    
    add_heading_styled(doc, "2.2. Cơ chế biến thiên quang học phản ánh nhịp thở", level=2)
    add_paragraph_styled(doc, "Quá trình hô hấp của con người tác động gián tiếp lên tín hiệu quang thể tích thông qua ba cơ chế sinh lý phối hợp:")
    add_paragraph_styled(doc, "1. Biến thiên cường độ do hô hấp (RIIV): Áp lực lồng ngực thay đổi trong chu kỳ hít vào và thở ra làm dịch chuyển đường nền của tín hiệu quang thể tích.")
    add_paragraph_styled(doc, "2. Biến thiên biên độ do hô hấp (RIAV): Thể tích tống máu của tim giảm khi hít vào và tăng khi thở ra do biến thiên áp lực đổ đầy tâm thất, tạo ra sự điều chế biên độ đỉnh-đáy của xung tim.")
    add_paragraph_styled(doc, "3. Biến thiên tần số do hô hấp (RIFV): Hiện tượng loạn nhịp xoang hô hấp khiến tim đập nhanh hơn khi hít vào và chậm lại khi thở ra.")
    add_paragraph_styled(doc, "Đề tài sử dụng ngữ cảnh 32 giây để quan sát biến thiên chậm liên quan đến hô hấp. Bộ đọc yêu cầu ít nhất 24 giây đặc trưng hữu dụng. Đây là lựa chọn của quy trình này, không phải thời lượng tối thiểu đúng cho mọi phương pháp ước lượng nhịp thở [1, 2, 6].")
    
    add_heading_styled(doc, "2.3. Phương pháp nén biến đổi cosin rời rạc DCT-1D", level=2)
    add_paragraph_styled(doc, "Biến đổi cosin rời rạc loại II (DCT-1D) là kỹ thuật nén truyền thống phổ biến trong xử lý tín hiệu y sinh. Đối với khối tín hiệu chuẩn hóa chiều dài N mẫu, các hệ số biến đổi được tính theo công thức:")
    add_formula_block(doc, "Xₖ = αₖ ∑ₙ₌₀ᴺ⁻¹ xₙ cos[π(2n + 1)k / (2N)]", "(2.1)")
    add_paragraph_styled(doc, "Biến đổi dùng chuẩn hóa trực giao: hệ số α₀ bằng căn của 1/N, còn αₖ bằng căn của 2/N với k lớn hơn 0. Bộ đối chứng giữ cùng số hệ số, cùng lượng tử hóa và cùng định dạng gói như mô hình học.")
    add_paragraph_styled(doc, "Phương pháp nén đối chứng giữ lại L_z hệ số DCT đầu tiên theo thứ tự chỉ số và gán các hệ số còn lại bằng 0. Đây là đường cơ sở xác định, không cần huấn luyện. Mức độ bảo toàn dạng sóng và các thành phần sinh lý ở từng tỷ lệ nén được xác định bằng kết quả thực nghiệm, không suy ra chỉ từ vị trí hệ số được giữ lại.")
    
    add_heading_styled(doc, "2.4. Mạng nơ-ron tích chập tự mã hóa một chiều", level=2)
    add_paragraph_styled(doc, "Mạng tự mã hóa học biểu diễn tín hiệu bằng một vectơ có ít thành phần hơn, sau đó dùng bộ giải mã để tái tạo dạng sóng. Các tầng tích chập một chiều khai thác quan hệ giữa các mẫu lân cận và dùng chung trọng số. Đề tài chưa thực hiện đối chứng với mạng kết nối đầy đủ, nên không kết luận kiến trúc này tốt hơn kiến trúc đó.")
    
    add_heading_styled(doc, "2.5. Cơ chế ước lượng gradient thẳng phục vụ lượng tử hóa số nguyên", level=2)
    add_paragraph_styled(doc, "Để nạp biểu diễn không gian ẩn vào khung truyền nhị phân, các giá trị số thực 32-bit phải được lượng tử hóa về số nguyên có dấu 16-bit (INT16). Với mỗi vector z, lượng tử hóa đối xứng theo hệ số riêng của gói theo công thức (4):")
    add_cloned_formula(doc, 71, "a = max(max|z|/32767, 10^-8),  q = clip(round(z/a), -32767, 32767),  z_tilde = a * q", "(4)")
    add_paragraph_styled(doc, "Phép làm tròn số nguyên có đạo hàm bằng 0 tại hầu hết mọi điểm, gây triệt tiêu gradient lan truyền ngược. Đề tài áp dụng cơ chế ước lượng gradient thẳng (STE) với xấp xỉ gradient truyền thẳng trong lan truyền ngược:")
    add_formula_block(doc, "∂z̃ / ∂z ≈ 1", "")
    add_paragraph_styled(doc, "Đạo hàm xấp xỉ cho phép gradient đi qua phép làm tròn trong huấn luyện. Khi suy luận, hệ thống sử dụng lượng tử hóa và gói nhị phân thực, không dùng gradient [4].")
    
    add_heading_styled(doc, "2.6. Hàm mất mát ràng buộc đa thang phổ thời gian và tần số", level=2)
    add_paragraph_styled(doc, "Hàm mất mát gồm sai số thời gian và sai lệch phổ biên độ ở hai độ dài 8 và 32 giây. Với ngữ cảnh có bốn khối, sai số thời gian được chuẩn hóa cục bộ theo công thức (6):")
    add_cloned_formula(doc, 81, "L_time = (1/4N) sum_j sum_n ((x_j[n] - x_hat_j[n]) / s_j)^2", "(6)")
    add_paragraph_styled(doc, "Với mỗi đoạn tín hiệu, số mẫu bằng thời lượng nhân với tần số lấy mẫu 125 Hz. Sau khi loại trung bình và áp dụng cửa sổ Hann, biên độ phổ được chia cho tổng các hệ số cửa sổ theo công thức (7):")
    add_cloned_formula(doc, 83, "A_T(v)[k] = |rFFT{h_T * (v - v_bar)}[k]| / sum_n h_T[n],  f_k = 125 k / N_T", "(7)")
    add_paragraph_styled(doc, "Sai lệch phổ được chuẩn hóa theo năng lượng tham chiếu. Hằng số ổn định bằng 10⁻⁸; thành phần tần số bằng không được bỏ ở cả tử số và mẫu số theo công thức (8):")
    add_cloned_formula(doc, 85, "S_T(v, v_hat; w_T) = sum_k w_T[k] (A_T(v)[k] - A_T(v_hat)[k])^2 / (sum_k w_T[k] A_T(v)[k]^2 + eps_f)", "(8)")
    add_paragraph_styled(doc, "Phổ 8 giây có trọng số 2 trong dải nhịp tim 0,8–3,0 Hz; phổ 32 giây có trọng số 4 trong dải nhịp thở 0,1–0,4 Hz. Các tần số khác có trọng số 1. Chuỗi 32 giây được ghép từ bốn khối đã hoàn nguyên biên độ. Hệ số của thành phần phổ trong mất mát tổng bằng 0,5 theo công thức (9):")
    add_cloned_formula(doc, 89, "L_spec = 0.5 [ 0.25 sum_j S_8 + S_32 ],  L = L_time + beta L_spec", "(9)")
    
    # -------------------------------------------------------------------------
    # CHƯƠNG 3
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "CHƯƠNG 3. THIẾT KẾ HỆ THỐNG VÀ PHƯƠNG PHÁP ĐỀ XUẤT", level=1)
    
    add_heading_styled(doc, "3.1. Kiến trúc tổng thể hệ thống thu thập và xử lý tín hiệu", level=2)
    add_paragraph_styled(doc, "Thiết kế tách phía mã hóa và phía giải mã theo định hướng thiết bị đeo. Trong phạm vi thực nghiệm, cả hai phía chạy trên máy tính và truyền gói qua UDP cục bộ; chưa triển khai truyền vô tuyến trên vi điều khiển. Hình 3.1 phân biệt luồng suy luận với luồng huấn luyện.")
    
    fig_pipe_path = os.path.join(FIG_DIR, "fig_pipeline.png")
    add_picture_styled(doc, fig_pipe_path, width=Inches(6.2), 
                       alt_text="Sơ đồ kiến trúc tổng thể hệ thống nén tín hiệu PPG tại thiết bị biên và giải mã hoàn nguyên tại máy chủ đám mây",
                       caption_text="Hình 3.1: Sơ đồ kiến trúc tổng thể hệ thống nén biên và giải mã máy chủ",
                       bookmark_name="BM_FIG_3_1")
        
    add_heading_styled(doc, "3.2. Tiền xử lý tín hiệu số nhân quả và cơ chế chuẩn hóa khối", level=2)
    add_paragraph_styled(doc, "Nhằm đáp ứng nguyên lý triển khai thời gian thực trên vi điều khiển nhúng, các bước xử lý tín hiệu số được thiết kế nhân quả nghiêm ngặt:")
    add_paragraph_styled(doc, "1. Lọc thông dải nhân quả: Tín hiệu gốc 125 Hz đi qua bộ lọc Butterworth 0,05–8,0 Hz với tham số bậc N bằng 2, tương ứng bậc thông dải tổng bằng 4. Cấu trúc các đoạn bậc hai nối tiếp giúp duy trì trạng thái lọc theo thời gian; không sử dụng mẫu tương lai.")
    with open(os.path.join(BASE_DIR, "data", "processed", "dev_uncompressed_baseline.json"), encoding="utf-8") as evidence_file:
        dev_evidence = json.load(evidence_file)
    warm_evidence = dev_evidence["warmup_transient_evidence"]
    add_paragraph_styled(doc, f"2. Loại bỏ giai đoạn khởi động: Bỏ 32 giây đầu mỗi đoạn liên tục và khởi tạo lại trạng thái sau khoảng mất mẫu. Đáp ứng bước duy trì dưới 1% biên độ đỉnh sau {warm_evidence['transient_settling_time_to_1pct_sec']:.2f} giây trong khoảng quan sát 120 giây. Tại giây 32, biên độ còn {100 * warm_evidence['transient_fraction_of_peak_at_warmup']:.4f}% đỉnh; đây là sai lệch nhỏ, không phải bằng không.")
    add_paragraph_styled(doc, "3. Chia khối và chuẩn hóa: Mỗi khối 8 giây có 1.000 mẫu. Tính trung bình và thang chuẩn hóa theo công thức (2); chuẩn hóa và hoàn nguyên theo công thức (3):")
    add_cloned_formula(doc, 55, "s_j = sqrt( (1/(N-1)) sum_n (x_j[n] - mu_j)^2 + eps_std )", "(1)")
    add_cloned_formula(doc, 56, "x_j^norm[n] = (x_j[n] - mu_j)/s_j,  x_hat_j[n] = x_hat_j^norm[n] * s_tilde_j + mu_tilde_j", "(2, 3)")
    add_paragraph_styled(doc, "Hai tham số mu và s được đính kèm vào phần đầu gói tin nhị phân dưới dạng số thực 32-bit FLOAT32 để phục vụ hoàn nguyên chính xác tại máy chủ.")
    
    add_heading_styled(doc, "3.3. Thiết kế kiến trúc bộ mã hóa nén tại biên", level=2)
    add_paragraph_styled(doc, "Bộ mã hóa nhận khối tín hiệu chuẩn hóa kích thước 1x1000 và nén về không gian ẩn L_z chiều thông qua các tầng tích chập một chiều và tầng tuyến tính thu gọn:")
    
    arch_headers = ["Tầng xử lý", "Loại tầng", "Số kênh vào / ra", "Kích thước nhân", "Bước nhảy", "Kích thước đầu ra"]
    arch_data = [
        ["Đầu vào", "Khối chuẩn hóa", "1 / 1", "-", "-", "1 x 1000"],
        ["Tầng E1", "Conv1D + ReLU", "1 / 16", "7", "2", "16 x 500"],
        ["Tầng E2", "Conv1D + ReLU", "16 / 32", "7", "2", "32 x 250"],
        ["Tầng E3", "Conv1D 1x1", "32 / 1", "1", "1", "1 x 250"],
        ["Thu gọn", "Tuyến tính", "250 / L_z", "-", "-", "1 x L_z (115 hoặc 52)"],
        ["Lượng tử", "Lượng tử STE", "1 / 1", "-", "-", "1 x L_z (INT16, Thang a)"]
    ]
    create_table_styled(doc, arch_headers, arch_data, col_widths=[1.0, 1.6, 1.2, 1.0, 0.8, 1.4], bookmark_name="BM_TBL_3_1")
    add_caption_styled(doc, "Bảng 3.1: Thông số kiến trúc chi tiết của mạng nơ-ron tích chập tự mã hóa")
    
    add_heading_styled(doc, "3.4. Định dạng khung truyền nhị phân và kiểm tra toàn vẹn CRC-16", level=2)
    add_paragraph_styled(doc, "Để đảm bảo tương thích phần cứng nhúng và đo đạc tỷ số nén thực tế theo đúng định dạng, cấu trúc gói tin nhị phân bao gồm phần đầu cố định 18 byte trước CRC, tiếp theo là tải trọng không gian ẩn số nguyên 16-bit và kết thúc bằng mã kiểm tra CRC-16-CCITT 2 byte:")
    
    fig_pkt_path = os.path.join(FIG_DIR, "fig_packet_format.png")
    add_picture_styled(doc, fig_pkt_path, width=Inches(6.2),
                       alt_text="Sơ đồ cấu trúc khung truyền gói tin nhị phân định dạng cố định chuẩn thiết bị nhúng bao gồm 18 byte phần đầu, tải trọng không gian ẩn và 2 byte mã kiểm tra CRC-16",
                       caption_text="Hình 3.2: Cấu trúc khung truyền gói tin nhị phân chuẩn thiết bị nhúng",
                       bookmark_name="BM_FIG_3_2")
        
    pkt_headers = ["Trường dữ liệu", "Kích thước", "Kiểu dữ liệu", "Chức năng kỹ thuật"]
    pkt_data = [
        ["Phiên bản", "1 byte", "UInt8", "Định danh phiên bản giao thức gói tin: 0x01"],
        ["Mã cấu hình", "1 byte", "UInt8", "0x01 cho mức 8 lần; 0x02 cho mức 16 lần"],
        ["Số thứ tự", "4 byte", "UInt32", "Phát hiện mất gói, lặp gói hoặc sai thứ tự"],
        ["Trung bình μ", "4 byte", "Float32", "Giá trị trung bình khối phục vụ hoàn nguyên biên độ"],
        ["Thang s", "4 byte", "Float32", "Độ lệch chuẩn khối phục vụ hoàn nguyên biên độ"],
        ["Hệ số a", "4 byte", "Float32", "Hệ số lượng tử hóa dùng khi giải mã không gian ẩn"],
        ["Tải trọng z_q", "2L_z byte", "Int16", "230 byte với L_z = 115; 104 byte với L_z = 52"],
        ["CRC-16", "2 byte", "UInt16", "Mã kiểm tra toàn vẹn CRC-16-CCITT, đa thức 0x1021"]
    ]
    create_table_styled(doc, pkt_headers, pkt_data, col_widths=[1.4, 1.1, 1.0, 3.1], bookmark_name="BM_TBL_3_2")
    add_caption_styled(doc, "Bảng 3.2: Cấu trúc trường dữ liệu chi tiết của khung truyền nhị phân cố định")
    
    add_paragraph_styled(doc, "Chuẩn đối chiếu là 1.000 mẫu PPG biểu diễn 16 bit/mẫu (2.000 byte). Tỷ số nén ở tầng ứng dụng với phần cố định 20 byte kể cả CRC-16 cuối gói được tính theo công thức (5):")
    add_cloned_formula(doc, 76, "CR = 2000 / (20 + 2 L_z)", "(5)")
    add_paragraph_styled(doc, "Tổng dung lượng gói tin ở tỷ lệ nén 8 lần là 18 byte phần đầu + 230 byte tải trọng + 2 byte CRC = 250 byte, tỷ số nén thực tế đạt đúng 2000 / 250 = 8.000 lần. Ở tỷ lệ nén 16.1 lần, dung lượng gói tin là 18 byte phần đầu + 104 byte tải trọng + 2 byte CRC = 124 byte, tỷ số nén đạt đúng 2000 / 124 = 16.129 lần.")
    
    add_heading_styled(doc, "3.5. Kiến trúc bộ giải mã hoàn nguyên tín hiệu tại máy chủ", level=2)
    add_paragraph_styled(doc, "Sau kiểm tra gói, tải trọng số nguyên được nhân với hệ số lượng tử đọc từ gói theo công thức (4). Vectơ ẩn được mở rộng lên 250 chiều rồi đi qua các tầng tích chập chuyển vị để tái tạo 1.000 mẫu. Đầu ra bộ giải mã được nhân với thang chuẩn hóa và cộng trung bình đọc từ gói theo công thức (3).")
    
    add_heading_styled(doc, "3.6. Thuật toán ước lượng nhịp tim và tần số hô hấp đồng thuận", level=2)
    add_paragraph_styled(doc, "Quy trình trích xuất các chỉ số sinh lý trên tín hiệu giải nén được thực hiện như sau:")
    add_paragraph_styled(doc, "1. Nhịp tim: Tìm đỉnh trên khối 8 giây với khoảng cách tối thiểu tương ứng 210 nhịp/phút và độ nổi tối thiểu bằng 0,3 lần độ lệch chuẩn. Cần ít nhất ba đỉnh. Nhịp tim bằng 60 chia trung vị khoảng thời gian giữa các đỉnh liên tiếp, tính bằng giây.")
    add_paragraph_styled(doc, "2. Nhịp thở: Ghép bốn khối 8 giây liên tiếp, trích RIIV và RIAV, nội suy tuyến tính 4 Hz và tìm đỉnh phổ Welch trong dải 0,1–0,7 Hz. Cần ít nhất 24 giây hữu dụng và chênh lệch hai ước lượng không quá 3 nhịp thở/phút. Ngưỡng này khác ngưỡng 2 nhịp thở/phút dùng để kiểm tra đồng thuận nhãn của hai người chú thích.")
    
    # -------------------------------------------------------------------------
    # CHƯƠNG 4
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "CHƯƠNG 4. THỰC NGHIỆM VÀ KẾT QUẢ ĐÁNH GIÁ", level=1)
    
    add_heading_styled(doc, "4.1. Tập dữ liệu y tế chuẩn BIDMC và phân chia kiểm thử khóa", level=2)
    add_paragraph_styled(doc, f"Nghiên cứu sử dụng 53 bản ghi tín hiệu từ bộ dữ liệu chuẩn quốc tế BIDMC (PhysioNet), tương ứng 46 mã định danh người bệnh nguồn MIMIC II. Tín hiệu quang thể tích được ghi nhận với tần số lấy mẫu 125 Hz kèm nhãn nhịp tim đo chuẩn và nhãn các mốc thở được chú thích độc lập bởi hai chuyên gia y tế.")
    test_recs_str = ", ".join(str(r) for r in sorted(splits_info.get("test_records", [])))
    add_paragraph_styled(doc, f"Tập kiểm thử gồm {n_test_subs} đối tượng kiểm thử độc lập mang {n_test_recs} bản ghi ({test_recs_str}), hoàn toàn không có đối tượng trùng với {n_dev_subs} đối tượng phát triển ({n_dev_recs} bản ghi). Đánh giá dùng {n_test_ctx} ngữ cảnh 32 giây không chồng lấp, tương ứng {n_test_blocks} khối 8 giây. Các khối cùng đối tượng không được coi là các quan sát độc lập; thống kê được tổng hợp theo đối tượng người bệnh.")
    add_paragraph_styled(doc, "Tập kiểm thử đã được xem trong những lần chạy thử trước, vì vậy không được coi là tập kiểm thử chưa từng mở. Ở lần chạy hiện tại, cấu hình và số vòng huấn luyện cuối được khóa từ kiểm định trên tập phát triển, không điều chỉnh theo kết quả kiểm thử. Lịch sử chạy được giữ để đối chiếu.")
    add_paragraph_styled(doc, "Một giới hạn của hồ sơ là trường mã băm mã nguồn trong checkpoint đã được ghi đè sau huấn luyện ở lần chạy trước. Bản sửa này giữ nguyên các tệp mô hình và xác minh lại đánh giá từ chúng; không khẳng định đã khôi phục đầy đủ mã nguồn tại thời điểm huấn luyện. Bằng chứng sử dụng mô hình và giới hạn truy nguyên được lưu riêng, không thay bằng một mã băm tự gán.")
    add_paragraph_styled(doc, "Nhãn nhịp thở được tính độc lập cho từng người chú thích trong cửa sổ thời gian nửa kín. Với ít nhất ba mốc thở, tần số bằng số khoảng thở chia thời gian giữa mốc cuối và đầu, nhân 60. Chỉ lấy trung bình hai nhãn khi chênh lệch không quá 2 nhịp thở/phút; trường hợp không đạt được ghi thiếu [1].")
    add_equation(doc, custom_math("RR"), "RR")

    add_paragraph_styled(doc, f"Kiểm tra chất lượng trước mô hình ghi nhận {prep_audit['records_processed']}/{prep_audit['records_expected']} bản ghi được xử lý, {prep_audit['candidate_contexts']} ngữ cảnh ứng viên và {prep_audit['accepted_contexts']} ngữ cảnh được chấp nhận. Có {prep_audit['rejected_nonfinite_or_missing']} ngữ cảnh bị loại do thiếu mẫu hoặc chứa giá trị không hữu hạn và {prep_audit['rejected_flat_signal']} ngữ cảnh bị loại do độ lệch chuẩn thấp hơn {prep_audit['flat_std_min']:.4f}. Trong các mẫu tín hiệu hợp lệ, {prep_audit['contexts_missing_rr_label']} ngữ cảnh thiếu nhãn nhịp thở và {prep_audit['blocks_missing_hr_label']} khối thiếu nhãn nhịp tim; các mẫu này vẫn được giữ cho đánh giá dạng sóng nhưng không được đưa vào chỉ số sinh lý tương ứng.")
    
    add_heading_styled(doc, "4.2. Môi trường thực nghiệm và thiết lập siêu tham số", level=2)
    label_audit = res["evaluation"]["reference_label_audit"]
    add_paragraph_styled(doc, f"Kiểm tra nhãn trên tập kiểm thử ghi nhận {label_audit['hr']['valid']}/{label_audit['hr']['total']} nhãn nhịp tim hợp lệ và {label_audit['rr']['valid']}/{label_audit['rr']['total']} nhãn nhịp thở hợp lệ, cùng trên {n_test_subs} đối tượng kiểm thử. Số nhãn ngoài dải bộ đọc lần lượt là {label_audit['hr']['outside_reader_band']} và {label_audit['rr']['outside_reader_band']}. Nhãn hữu hạn, dương được giữ dù ngoài dải; nhãn thiếu không được thay bằng 0.")
    add_paragraph_styled(doc, "Mô hình được huấn luyện bằng PyTorch 2.6.0 trên bộ xử lý đồ họa NVIDIA GeForce RTX 3050 Ti Laptop. Bộ tối ưu AdamW dùng tốc độ học ban đầu 0,001 và hệ số suy giảm trọng số 0,0001. Trong kiểm định chéo, tốc độ học giảm một nửa sau 5 vòng không cải thiện và quá trình dừng sớm sau 15 vòng không cải thiện. Khi huấn luyện cuối trên toàn bộ tập phát triển, số vòng đã được khóa bằng trung vị số vòng tốt nhất của năm nếp nên không dùng dữ liệu kiểm thử để điều chỉnh lịch học. Kích thước lô là 16 ngữ cảnh, tương ứng 64 khối 8 giây; hạt giống ngẫu nhiên là 2026.")
    
    add_heading_styled(doc, "4.3. Kết quả kiểm định chéo năm nếp trên tập phát triển", level=2)
    add_paragraph_styled(doc, f"Kiểm định chéo năm nếp, chia nhóm theo đối tượng người bệnh bằng GroupKFold, được thực hiện riêng cho sáu cấu hình học máy trên {n_dev_subs} đối tượng phát triển ({n_dev_recs} bản ghi). Mỗi nếp chạy tối đa 150 vòng, giảm tốc độ học sau 5 vòng không cải thiện và dừng sớm sau 15 vòng không cải thiện.")
    
    cv_headers = ["Cấu hình", "Vòng tốt nhất của 5 nếp", "Trung vị số vòng", "Mất mát kiểm định: trung bình ± độ lệch chuẩn"]
    cv_data = []
    for cfg_name, cfg_cv in cv_per_config.items():
        epochs = ", ".join(str(f["best_epoch"]) for f in cfg_cv["folds"])
        cv_data.append([METHOD_DISPLAY_NAMES.get(cfg_name, cfg_name), epochs,
                        str(cfg_cv["median_best_epoch"]),
                        f"{cfg_cv['val_loss_mean']:.6f} ± {cfg_cv['val_loss_std']:.6f}"])
    med_ep = cv_info["median_best_epoch"]
    m_loss = cv_info["val_loss_mean"]
    s_loss = cv_info["val_loss_std"]
    create_table_styled(doc, cv_headers, cv_data, col_widths=[2.4, 2.0, 1.2, 2.4], bookmark_name="BM_TBL_4_1")
    add_caption_styled(doc, f"Bảng 4.1: Kết quả kiểm định chéo năm nếp trên tập phát triển ({n_dev_subs} đối tượng, {n_dev_recs} bản ghi)")
    
    epoch_summary = "; ".join(f"{METHOD_DISPLAY_NAMES.get(k, k)}: {v['median_best_epoch']}" for k, v in cv_per_config.items())
    add_paragraph_styled(doc, f"Mỗi mô hình cuối được huấn luyện trên toàn bộ {n_dev_subs} đối tượng phát triển ({n_dev_recs} bản ghi) bằng trung vị số vòng của chính cấu hình đó: {epoch_summary}. Tập kiểm thử không được dùng để dừng sớm.")
    
    add_heading_styled(doc, "4.4. Đánh giá chất lượng phục hồi dạng sóng và các chỉ số sinh học", level=2)
    add_paragraph_styled(doc, f"Bảng 4.2a, 4.2b và 4.2c tổng hợp kết quả đo đạc trên {n_test_ctx} ngữ cảnh 32 giây không chồng lấp ({n_test_blocks} khối 8 giây) của {n_test_subs} đối tượng kiểm thử độc lập ({n_test_recs} bản ghi). Chất lượng dạng sóng được đánh giá bằng độ méo phần trăm dư PRD và PRDc chuẩn hóa theo công thức (10):")
    add_cloned_formula(doc, 110, "PRD = 100 sqrt(sum e^2 / sum x^2),  PRDc = 100 sqrt(sum e^2 / sum (x - x_bar)^2)", "(10)")
    add_paragraph_styled(doc, "Tỷ số tín hiệu trên nhiễu sau hoàn nguyên SNRc được tính theo công thức (11):")
    add_cloned_formula(doc, 112, "SNRc = 10 log10( sum (x - x_bar)^2 / sum e^2 ) = 20 log10( 100 / PRDc )", "(11)")
    add_paragraph_styled(doc, "Sai số sinh lý đối với các bộ đọc nhịp tim HR và nhịp thở RR được tính bằng MAE theo công thức (12):")
    add_cloned_formula(doc, 115, "MAE_rec = (1/M) sum |r(x_hat_i) - y_i|,  MAE_orig = (1/M) sum |r(x_i) - y_i|", "(12)")
    add_paragraph_styled(doc, "Chênh lệch sai số ghép cặp ΔMAE và mức thay đổi đầu ra bộ đọc D_r so với tín hiệu không nén được định nghĩa theo công thức (13):")
    add_cloned_formula(doc, 116, "Delta_MAE = MAE_rec - MAE_orig,  D_r = (1/M) sum |r(x_hat_i) - r(x_i)|", "(13)")
    
    # -------------------------------------------------------------
    # BẢNG 4.2a: CHẤT LƯỢNG PHỤC HỒI DẠNG SÓNG QUANG THỂ TÍCH
    # -------------------------------------------------------------
    add_paragraph_styled(doc, f"Bảng 4.2a trình bày các chỉ số đánh giá độ méo dạng sóng gồm tỷ số nén thực tế CR, độ méo phần trăm dư PRD, độ méo phần trăm dư chuẩn hóa PRDc và tỷ số tín hiệu trên nhiễu SNRc trên {n_test_ctx} ngữ cảnh 32 giây ({n_test_blocks} khối 8 giây) của {n_test_subs} đối tượng kiểm thử độc lập ({n_test_recs} bản ghi). Giá trị công bố là giá trị trung bình đều theo từng đối tượng kèm khoảng tin cậy 95% được tính toán bằng 2.000 lần lấy mẫu lại theo đối tượng người bệnh:")

    wave_headers = ["Phương pháp", "Tỷ số nén", "PRD (%) [95% CI]", "PRDc (%) [95% CI]", "SNRc (dB) [95% CI]"]
    
    def get_wave_row(m_name, cr_str):
        if m_name not in eval_m:
            d_name = METHOD_DISPLAY_NAMES.get(m_name, m_name)
            raise ValueError("Thiếu phương pháp bắt buộc: " + m_name)
        m = eval_m[m_name]
        prdc = m["prdc_mean"]
        prdc_ci = m["prdc_ci"]
        prd = m["prd_mean"]
        prd_ci = m["prd_ci"]
        snrc = m["snrc_mean"]
        snrc_ci = m["snrc_ci"]
        d_name = METHOD_DISPLAY_NAMES.get(m_name, m_name)
        
        if m_name == "Uncompressed":
            return [d_name, cr_str, "0.00", "0.00", "∞ (Không méo)"]
            
        prd_str = f"{prd:.2f} ± {m['prd_std']:.2f}\n[{prd_ci[0]:.2f}, {prd_ci[1]:.2f}]"
        prdc_str = f"{prdc:.2f} ± {m['prdc_std']:.2f}\n[{prdc_ci[0]:.2f}, {prdc_ci[1]:.2f}]"
        snrc_str = f"{snrc:.2f} ± {m['snrc_std']:.2f}\n[{snrc_ci[0]:.2f}, {snrc_ci[1]:.2f}]"
        return [d_name, cr_str, prd_str, prdc_str, snrc_str]
        
    wave_data = [
        get_wave_row("Uncompressed", "1.00x"),
        get_wave_row("DCT-1D (8x)", "8.00x"),
        get_wave_row("DCT-1D (16x)", "16.13x"),
        get_wave_row("Autoencoder-MSE (8x)", "8.00x"),
        get_wave_row("Autoencoder-MSE (16x)", "16.13x"),
        get_wave_row("De xuat Day du (8x)", "8.00x"),
        get_wave_row("De xuat Day du (16x)", "16.13x")
    ]
    create_table_styled(doc, wave_headers, wave_data, col_widths=[2.1, 0.9, 1.8, 1.8, 1.8], bookmark_name="BM_TBL_4_2A")
    add_caption_styled(doc, "Bảng 4.2a: Kết quả đánh giá chất lượng phục hồi dạng sóng quang thể tích trên tập kiểm thử")
    add_paragraph_styled(doc, f"Ghi chú phương pháp: Mẫu số đánh giá gồm đúng {n_test_blocks} khối 8 giây trên {n_test_subs} đối tượng kiểm thử độc lập ({n_test_recs} bản ghi). Với tín hiệu chưa nén, sai số tái tạo bằng 0 nên SNRc được xác định bằng vô cùng theo định nghĩa, không thay bằng số thực hữu hạn.")

    # -------------------------------------------------------------
    # BẢNG 4.2b: CHỈ SỐ SINH HỌC TRÊN TẬP GIAO HỢP LỆ VÀ MỨC BIẾN ĐỔI Dr
    # -------------------------------------------------------------
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    add_paragraph_styled(doc, "Bảng 4.2b công bố kết quả sai số tuyệt đối trung bình nhịp tim MAE-HR và nhịp thở MAE-RR tính toán trên cùng tập giao các mẫu hợp lệ chung giữa toàn bộ các phương pháp đối chứng, đi kèm chỉ số mức thay đổi đầu ra sinh học Dr theo Công thức (13) và độ bao phủ của bộ ước lượng nhịp thở:")

    phys_headers = ["Phương pháp", "Tỷ số nén", "MAE-HR: nhịp/phút; KTC 95%", "Dr-HR: nhịp/phút; ± ĐLC", "MAE-RR: nhịp thở/phút; KTC 95%", "Dr-RR: nhịp thở/phút; ± ĐLC", "Độ bao phủ RR: %"]

    def get_phys_row(m_name, cr_str):
        if m_name not in eval_m:
            d_name = METHOD_DISPLAY_NAMES.get(m_name, m_name)
            raise ValueError("Thiếu phương pháp bắt buộc: " + m_name)
        m = eval_m[m_name]
        inter = m.get("intersection", {})
        indiv = m.get("individual", {})
        hr_m = inter["mae_hr_mean"]
        hr_ci = inter["mae_hr_ci"]
        rr_m = inter["mae_rr_mean"]
        rr_ci = inter["mae_rr_ci"]
        cov = indiv["rr_coverage_pct"]
        
        dr_hr_m = m["dr_hr_mean"]
        dr_hr_s = m["dr_hr_std"]
        dr_rr_m = m["dr_rr_mean"]
        dr_rr_s = m["dr_rr_std"]
        
        d_name = METHOD_DISPLAY_NAMES.get(m_name, m_name)
        if m_name == "Uncompressed":
            hr_str = f"{hr_m:.2f} ± {inter['mae_hr_std']:.2f}\n[{hr_ci[0]:.2f}, {hr_ci[1]:.2f}]"
            rr_str = f"{rr_m:.2f} ± {inter['mae_rr_std']:.2f}\n[{rr_ci[0]:.2f}, {rr_ci[1]:.2f}]"
            return [d_name, cr_str, hr_str, "0.00 ± 0.00 (Gốc)", rr_str, "0.00 ± 0.00 (Gốc)", f"{cov:.1f}%"]
            
        hr_str = f"{hr_m:.2f} ± {inter['mae_hr_std']:.2f}\n[{hr_ci[0]:.2f}, {hr_ci[1]:.2f}]"
        rr_str = f"{rr_m:.2f} ± {inter['mae_rr_std']:.2f}\n[{rr_ci[0]:.2f}, {rr_ci[1]:.2f}]"
        dr_hr_ci = m["dr_hr_ci"]
        dr_rr_ci = m["dr_rr_ci"]
        dr_hr_str = f"{dr_hr_m:.2f} ± {dr_hr_s:.2f}\n[{dr_hr_ci[0]:.2f}, {dr_hr_ci[1]:.2f}]"
        dr_rr_str = f"{dr_rr_m:.2f} ± {dr_rr_s:.2f}\n[{dr_rr_ci[0]:.2f}, {dr_rr_ci[1]:.2f}]"
        return [d_name, cr_str, hr_str, dr_hr_str, rr_str, dr_rr_str, f"{cov:.1f}%"]

    phys_data = [
        get_phys_row("Uncompressed", "1.00x"),
        get_phys_row("DCT-1D (8x)", "8.00x"),
        get_phys_row("DCT-1D (16x)", "16.13x"),
        get_phys_row("Autoencoder-MSE (8x)", "8.00x"),
        get_phys_row("Autoencoder-MSE (16x)", "16.13x"),
        get_phys_row("De xuat Day du (8x)", "8.00x"),
        get_phys_row("De xuat Day du (16x)", "16.13x")
    ]
    create_table_styled(doc, phys_headers, phys_data, col_widths=[1.7, 0.7, 1.5, 1.2, 1.5, 1.2, 0.8], bookmark_name="BM_TBL_4_2B")
    add_caption_styled(doc, "Bảng 4.2b: Kết quả ước lượng các chỉ số sinh lý trên tập giao hợp lệ giữa các phương pháp")
    add_paragraph_styled(doc, "Ghi chú: Dr là mức thay đổi tuyệt đối của đầu ra sinh lý theo công thức (13), khác với ΔMAE so với nhãn. Các cột sai số và Dr ghi trung bình ± độ lệch chuẩn theo người bệnh. Dòng trong ngoặc vuông ghi khoảng tin cậy 95% từ 2.000 lần lấy mẫu lại người bệnh. Độ bao phủ dùng số ngữ cảnh hợp lệ chia số ngữ cảnh có nhãn hợp lệ, không phải tỷ lệ người bệnh.")

    # -------------------------------------------------------------
    # BẢNG 4.2c: THỐNG KÊ CHI TIẾT TẬP GIAO VÀ LÝ DO LOẠI BỎ
    # -------------------------------------------------------------
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    add_paragraph_styled(doc, "Bảng 4.2c công bố chi tiết số lượng mẫu, tỷ lệ tập giao hợp lệ và phân loại các lý do thất bại của bộ ước lượng nhịp thở:")
    
    inter_headers = ["Nội dung thống kê tập giao", "Đơn vị tính", "Giá trị đo đạc thực tế", "Ghi chú phương pháp"]
    inter_data = [
        ["Tổng số khối kiểm thử nhịp tim", "Khối 8 giây", f"{inter_info['total_test_blocks']}", f"{n_test_ctx} ngữ cảnh x 4 khối liên tiếp"],
        ["Tập giao nhịp tim (HR) hợp lệ", "Khối 8 giây", f"{inter_info['hr_intersection_blocks']}", f"Đạt tỷ lệ {inter_info['hr_intersection_pct']:.1f}% trên {n_test_subs}/{n_test_subs} đối tượng"],
        ["Tổng số ngữ cảnh kiểm thử nhịp thở", "Ngữ cảnh 32 giây", f"{inter_info['total_test_contexts']}", f"{n_test_recs} bản ghi x 14 ngữ cảnh không chồng lấp"],
        ["Tập giao nhịp thở (RR) hợp lệ", "Ngữ cảnh 32 giây", f"{inter_info['rr_intersection_contexts']}", f"Tỷ lệ {inter_info['rr_intersection_pct']:.1f}% trên toàn bộ {n_test_ctx} ngữ cảnh"],
        ["Số đối tượng có RR trong tập giao", "Đối tượng", f"{inter_info['rr_intersection_patients']} / {n_test_subs}", f"Gồm các đối tượng có mã số {', '.join(str(p) for p in inter_info['rr_valid_patients_list'])}"],
        ["Cơ chế tính khoảng tin cậy RR", "Bootstrap", "2.000 mẫu lại", f"Khoảng tin cậy 95% tính trên {inter_info['rr_intersection_patients']} đối tượng có mẫu hợp lệ"],
        ["Lý do thất bại bộ ước lượng RR", "Tiêu chuẩn loại", "Thời lượng hữu dụng < 24s hoặc chênh lệch RIIV/RIAV > 3.0 brpm", "Bảo toàn tính trung thực, không thay thế giá trị thiếu bằng 0"]
    ]
    create_table_styled(doc, inter_headers, inter_data, col_widths=[2.4, 1.1, 1.2, 2.7], bookmark_name="BM_TBL_4_2C")
    add_caption_styled(doc, "Bảng 4.2c: Thống kê chi tiết mẫu số đánh giá tập giao và lý do loại bỏ tín hiệu")
    
    fig_comp_path = os.path.join(FIG_DIR, "fig1_comparison_8x.png")
    add_picture_styled(doc, fig_comp_path, width=Inches(6.0),
                       alt_text="Biểu đồ cột so sánh độ méo phần trăm dư chuẩn hóa PRDc và sai số tuyệt đối trung bình nhịp thở MAE giữa các phương pháp nén ở tỷ lệ nén 8 lần",
                       caption_text="Hình 4.1: So sánh độ méo PRDc và sai số nhịp thở MAE giữa các mô hình ở tỷ lệ nén 8 lần",
                       bookmark_name="BM_FIG_4_1")
        
    add_paragraph_styled(doc, f"Trên tín hiệu gốc chưa nén, sai số cơ sở đạt MAE-HR là {uncomp_hr_inter:.2f} nhịp/phút và MAE-RR là {uncomp_rr_inter:.2f} nhịp thở/phút trên tập giao hợp lệ (khi xét toàn bộ cửa sổ riêng hợp lệ, MAE-RR của tín hiệu gốc là {uncomp_rr_indiv:.2f} nhịp thở/phút với độ bao phủ {uncomp_cov:.1f}%).")
    add_paragraph_styled(doc, f"Ở mức nén 16,1 lần, MAE-HR là {p16_hr_inter:.2f} nhịp/phút, với khoảng tin cậy 95% từ {p16_hr_inter_ci[0]:.2f} đến {p16_hr_inter_ci[1]:.2f}. Tín hiệu chưa nén có MAE-HR {uncomp_hr_inter:.2f} nhịp/phút trên cùng tập giao. Chênh lệch MAE ghép cặp là {p16_delta_hr:.2f} nhịp/phút, với khoảng tin cậy từ {p16_delta_hr_ci[0]:.2f} đến {p16_delta_hr_ci[1]:.2f}. {hr_difference_text}")
    
    with open(os.path.join(FIG_DIR, 'provenance.json'), encoding='utf-8') as stream:
        figure_evidence = json.load(stream)
    add_paragraph_styled(doc, f"Hình 4.2 minh họa bốn giây đầu của ngữ cảnh bắt đầu tại giây {figure_evidence['t_start']:g}, bản ghi {figure_evidence['record_id']}, người bệnh nguồn {figure_evidence['subject_id']}. Mẫu được lấy đầu tiên theo thứ tự mã người bệnh, bản ghi và thời gian, không chọn theo sai số nhỏ nhất.")
    fig_wave_path = os.path.join(FIG_DIR, "fig_waveform_reconstruction.png")
    add_picture_styled(doc, fig_wave_path, width=Inches(6.0),
                       alt_text="Biểu đồ dạng sóng quang thể tích thời gian so sánh giữa tín hiệu gốc chưa nén và tín hiệu tái tạo sau giải nén ở các mức nén khác nhau",
                       caption_text="Hình 4.2: Dạng sóng quang thể tích tái tạo so với dạng sóng gốc chưa nén",
                       bookmark_name="BM_FIG_4_2")
        
    add_heading_styled(doc, "4.5. Phân tích bóc tách các thành phần mất mát phổ", level=2)
    add_paragraph_styled(doc, "Nhằm làm rõ vai trò của từng thành phần trong hàm mất mát đa thang phổ đề xuất, hai biến thể bóc tách được đánh giá đối chứng cùng mô hình chuẩn ở tỷ lệ nén 8 lần:")
    add_paragraph_styled(doc, "1. Biến thể B (Phổ đều không trọng số): w_hr = 1.0, w_rr = 1.0.")
    add_paragraph_styled(doc, "2. Biến thể C (Ưu tiên dải nhịp tim): w_hr = 2.0, w_rr = 1.0.")
    
    ab_headers = ["Cấu hình thử nghiệm", "Trọng số (w_hr, w_rr)", "PRDc (%)", "MAE-HR (bpm)", "MAE-RR (brpm)", "Độ bao phủ RR (%)"]
    
    def get_ab_row(name, weight_str):
        m = eval_m[name]
        inter = m.get("intersection", {})
        indiv = m.get("individual", {})
        prdc = m["prdc_mean"]
        hr_m = inter["mae_hr_mean"]
        rr_m = inter["mae_rr_mean"]
        cov = indiv["rr_coverage_pct"]
        d_name = METHOD_DISPLAY_NAMES.get(name, name)
        return [d_name, weight_str, f"{prdc:.2f}%", f"{hr_m:.2f}", f"{rr_m:.2f}", f"{cov:.1f}%"]
        
    ab_data = [
        get_ab_row("De xuat Day du (8x)", "w_hr = 2.0, w_rr = 4.0"),
        get_ab_row("Boc tach B: Pho deu (8x)", "w_hr = 1.0, w_rr = 1.0"),
        get_ab_row("Boc tach C: Uu tien HR (8x)", "w_hr = 2.0, w_rr = 1.0"),
        get_ab_row("Autoencoder-MSE (8x)", "Không ràng buộc phổ")
    ]
    create_table_styled(doc, ab_headers, ab_data, col_widths=[1.8, 1.6, 0.9, 1.1, 1.1, 1.1], bookmark_name="BM_TBL_4_3")
    add_caption_styled(doc, "Bảng 4.3: Kết quả phân tích bóc tách các thành phần mất mát phổ")
    
    add_paragraph_styled(doc, "Hình 4.3 dùng toàn bộ ngữ cảnh 32 giây gồm 4.000 mẫu của cùng bản ghi. Cửa sổ Hann, độ dài đoạn và số điểm biến đổi đều bằng 4.000, không chèn thêm mẫu bằng không; khoảng cách tần số là 0,03125 Hz. Miền gạch chéo chỉ dải ưu tiên của hàm mất mát, không phải phổ riêng của nhịp thở.")
    fig_psd_path = os.path.join(FIG_DIR, "fig_spectrum_psd.png")
    add_picture_styled(doc, fig_psd_path, width=Inches(6.0),
                       alt_text="Biểu đồ mật độ phổ công suất PSD so sánh năng lượng phổ giữa tín hiệu gốc và tín hiệu tái tạo trong dải tần số hô hấp và tần số nhịp tim",
                       caption_text="Hình 4.3: Mật độ phổ công suất tín hiệu gốc và tín hiệu tái tạo sau giải nén",
                       bookmark_name="BM_FIG_4_3")
        
    add_heading_styled(doc, "4.6. Kiểm thử độ bền vững trước lỗi truyền thông gói tin bằng mã CRC-16", level=2)
    add_paragraph_styled(doc, f"Cơ chế bảo vệ gói tin được kiểm thử qua {bench['bit_flip_test']['trials']:,} phép thử lật 1 bit ngẫu nhiên trên toàn bộ chiều dài gói tin 250 byte với hạt giống cố định {bench['bit_flip_test']['seed']}:")
    
    crc_headers = ["Kịch bản gói tin", "Chiều dài", "Mã CRC tính toán", "Trạng thái", "Hành động hệ thống"]
    crc_data = [
        ["Gói tin chuẩn hợp lệ", "250 byte", "Trùng khớp mã phát", "Hợp lệ", "Chấp nhận giải mã"],
        [f"{bench['bit_flip_test']['trials']:,} gói tin lật 1 bit ngẫu nhiên", "250 byte", f"Không khớp ({bench['bit_flip_test']['detection_rate_pct']:.2f}%)", "Lỗi bit", "Hủy bỏ gói tin, kích hoạt cảnh báo"]
    ]
    create_table_styled(doc, crc_headers, crc_data, col_widths=[1.8, 1.0, 1.4, 1.2, 1.6], bookmark_name="BM_TBL_4_4")
    add_caption_styled(doc, "Bảng 4.4: Kết quả kiểm thử tính bền vững trước lỗi truyền thông gói tin bằng mã CRC-16")
    
    add_heading_styled(doc, "4.7. Đo đạc tài nguyên tính toán và độ trễ tham chiếu trên CPU", level=2)
    add_paragraph_styled(doc, "Hai bộ mã hóa được đo trên CPU máy tính sau 20 lượt khởi động và 100 lượt đo. Đầu vào là kênh PLETH gốc từ tệp WFDB, chuyển sang số thực 32 bit và chia khối 8 giây liên tiếp không chồng lấp. Trạng thái lọc của từng khối được xác lập từ đoạn tín hiệu trước đó, đặt lại giữa người bệnh hoặc sau mất mẫu và bỏ 32 giây khởi động. Không lọc lại tín hiệu đã tiền xử lý, không cộng thời gian chờ thu nhận vào thời gian tính toán.")
    add_paragraph_styled(doc, "Phép đo thời gian tắt theo dõi cấp phát bộ nhớ Python; bộ nhớ được đo ở một lượt riêng. Tín hiệu đầu vào và trọng số dùng số thực 32 bit, nhưng hệ số, trạng thái lọc và các bước tính toán trước khi đưa vào mạng dùng số thực 64 bit. Dung lượng bộ đệm là phép tính từ kích thước mảng, không phải phép đo đỉnh RAM vi điều khiển.")

    add_paragraph_styled(doc, f"Môi trường đo dùng {bench['environment']['torch_intraop_threads']} luồng nội bộ và {bench['environment']['torch_interop_threads']} luồng liên phép toán của PyTorch {bench['environment']['pytorch_version']}, Python {bench['environment']['python_version']}. RSS là bộ nhớ tiến trình tại thời điểm trước và sau phép đo, không phải đỉnh bộ nhớ. Công cụ tracemalloc chỉ quan sát cấp phát Python, không đo toàn bộ vùng nhớ gốc của thư viện.")
    
    bench_8x = bench.get("bench_8x", bench)
    bench_16x = bench.get("bench_16x", {})
    has_16x = bool(bench_16x and "enc_median_ms" in bench_16x)
    
    hw_headers = ["Công đoạn / Thông số kỹ thuật", "Ngân sách đề cương", "Đo tham chiếu 8x (Lz=115)", "Đo tham chiếu 16x (Lz=52)", "Đánh giá kỹ thuật"]
    
    hw_data = [
        ["Môi trường chạy tham chiếu", "Công bố cấu hình", 
         f"CPU {bench['environment']['cpu_arch']}; {bench['environment']['physical_cpu_count']} lõi vật lý", 
         f"CPU {bench['environment']['cpu_arch']}; {bench['environment']['physical_cpu_count']} lõi vật lý", 
         "Định dạng TorchScript, trọng số FLOAT32, tải gói INT16"],
        ["Kích thước tệp mô hình TorchScript", "<= 256 KiB", 
         f"{bench_8x['model_size_kb']:.2f} KiB", 
         f"{bench_16x['model_size_kb']:.2f} KiB" if has_16x else "Chưa đo", 
         "Đo thực tế trên tệp .pt, chưa có số liệu MCU sau biên dịch"],
        ["Dung lượng tham số trọng số", "Tham chiếu PC", 
         f"{bench_8x['weight_size_kb']:.2f} KiB ({bench_8x['num_params']:,} tham số)", 
         f"{bench_16x['weight_size_kb']:.2f} KiB ({bench_16x['num_params']:,} tham số)" if has_16x else "Chưa đo", 
         "Bộ mã hóa 1D-CNN thu gọn với tích chập 1x1"],
        ["Bộ nhớ tiến trình máy tính RSS", "Tham chiếu PC", 
         f"{bench_8x['host_process_rss_mb']:.2f} MiB (Tăng {bench_8x['host_process_rss_delta_mb']:.2f} MiB)", 
         f"{bench_16x['host_process_rss_mb']:.2f} MiB (Tăng {bench_16x['host_process_rss_delta_mb']:.2f} MiB)" if has_16x else "Chưa đo", 
         "Bộ nhớ tiến trình trên máy tính, không đại diện RAM vi điều khiển"],
        ["Đỉnh cấp phát bộ nhớ Python (tracemalloc)", "Tham chiếu Python", 
         f"{bench_8x['python_tracemalloc_peak_mb']:.4f} MiB", 
         f"{bench_16x['python_tracemalloc_peak_mb']:.4f} MiB" if has_16x else "Chưa đo", 
         "Chỉ theo dõi cấp phát do Python quản lý"],
        ["Tiền xử lý nhân quả (Lọc Butterworth SOS)", "-", 
         f"{bench_8x['prep_median_ms']:.3f} ms (P95: {bench_8x['prep_p95_ms']:.3f} ms)", 
         f"{bench_16x['prep_median_ms']:.3f} ms (P95: {bench_16x['prep_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "Lọc dải thông 0.05-8Hz cập nhật trạng thái zi nhân quả"],
        ["Chuẩn hóa Z-score cục bộ (Khối 8s)", "-", 
         f"{bench_8x['norm_median_ms']:.3f} ms (P95: {bench_8x['norm_p95_ms']:.3f} ms)", 
         f"{bench_16x['norm_median_ms']:.3f} ms (P95: {bench_16x['norm_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "Tính mu và s cục bộ cho 1.000 mẫu"],
        ["Suy luận bộ mã hóa 1D-CNN", "-", 
         f"{bench_8x['enc_median_ms']:.3f} ms (P95: {bench_8x['enc_p95_ms']:.3f} ms)", 
         f"{bench_16x['enc_median_ms']:.3f} ms (P95: {bench_16x['enc_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "Thời gian thực tế trên CPU máy tính"],
        ["Lượng tử hóa INT16 và Thang a", "-", 
         f"{bench_8x['quant_median_ms']:.3f} ms (P95: {bench_8x['quant_p95_ms']:.3f} ms)", 
         f"{bench_16x['quant_median_ms']:.3f} ms (P95: {bench_16x['quant_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "Lượng tử hóa đối xứng sang INT16 [-32767, 32767]"],
        ["Đóng gói khung truyền và tính CRC-16", "-", 
         f"{bench_8x['pack_median_ms']:.3f} ms (P95: {bench_8x['pack_p95_ms']:.3f} ms)", 
         f"{bench_16x['pack_median_ms']:.3f} ms (P95: {bench_16x['pack_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "20 byte cố định + tải trọng và mã kiểm tra CRC-16"],
        ["Truyền UDP vòng lặp nội bộ", "Tham chiếu PC", 
         f"{bench_8x['host_udp_loopback_median_ms']:.3f} ms (P95: {bench_8x['host_udp_loopback_p95_ms']:.3f} ms)", 
         f"{bench_16x['host_udp_loopback_median_ms']:.3f} ms (P95: {bench_16x['host_udp_loopback_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "UDP cục bộ trên máy tính, chưa phải vô tuyến phần cứng"],
        ["Bộ đệm đầu vào / tiềm ẩn / gói tin", "Tham chiếu bộ đệm", 
         f"{bench_8x['input_buffer_bytes']} / {bench_8x['latent_buffer_bytes']} / {bench_8x['packet_buffer_bytes']} byte", 
         f"{bench_16x['input_buffer_bytes']} / {bench_16x['latent_buffer_bytes']} / {bench_16x['packet_buffer_bytes']} byte" if has_16x else "Chưa đo", 
         "Không bao gồm ngăn xếp và vùng nhớ khi chạy trên vi điều khiển"],
        ["Ngăn xếp và vùng làm việc tạm", "Phải đo trên đích", 
         "Chưa đo", "Chưa đo", "Không tách được trên Python trên máy tính; không suy đoán từ tensor"],
        ["Năng lượng và thời lượng pin", "Phải đo trên thiết bị", 
         "Chưa đo", "Chưa đo", "Chỉ kết luận giảm số byte gói tin tầng ứng dụng"],
        ["TỔNG THỜI GIAN TÍNH TOÁN BIÊN (Khối 8s)", "<= 20.0 ms", 
         f"{bench_8x['total_median_ms']:.3f} ms (P95: {bench_8x['total_p95_ms']:.3f} ms)", 
         f"{bench_16x['total_median_ms']:.3f} ms (P95: {bench_16x['total_p95_ms']:.3f} ms)" if has_16x else "Chưa đo", 
         "Tiền xử lý + Chuẩn hóa + Mã hóa + Lượng tử + Đóng gói"]
    ]
    create_table_styled(doc, hw_headers, hw_data, col_widths=[2.0, 1.0, 1.6, 1.6, 1.8], bookmark_name="BM_TBL_4_5")
    add_caption_styled(doc, "Bảng 4.5: Tài nguyên tính toán và độ trễ đo đạc tham chiếu trên môi trường CPU máy tính")
    
    fig_lat_path = os.path.join(FIG_DIR, "fig_latency_breakdown.png")
    add_picture_styled(doc, fig_lat_path, width=Inches(6.0),
                       alt_text="Biểu đồ thanh phân rã thời gian thực thi các công đoạn chuẩn hóa, suy luận tích chập, lượng tử hóa và đóng gói tính toán trên CPU máy tính",
                       caption_text="Hình 4.4: Phân rã thời gian thực thi tham chiếu trên CPU máy tính cho một khối 8 giây",
                       bookmark_name="BM_FIG_4_4")
        
    add_paragraph_styled(doc, "Cần nhấn mạnh rõ ràng rằng các phép đo trên được thực hiện trên môi trường CPU máy tính thông thường (x86-64), chưa phải kết quả triển khai và đo lường trực tiếp trên vi điều khiển nhúng chuyên dụng (như ARM Cortex-M4 của nRF52840 hoặc STM32WB55). Phép đo này không phản ánh dòng điện tiêu thụ, thời gian ngủ sâu, thời lượng pin hay cấu trúc bộ nhớ tĩnh/động của vi điều khiển.")

    add_heading_styled(doc, "4.8. Phân tích sai lệch dạng sóng và độ dốc tại các điểm nối khối", level=2)
    add_paragraph_styled(doc, f"Do tín hiệu ngữ cảnh 32 giây được ghép nối từ 4 khối 8 giây được mã hóa riêng, hiện tượng gián đoạn biên độ (bước nhảy biên độ) hoặc gãy khúc độ dốc (gián đoạn độ dốc) tại 3 điểm nối (mẫu thứ 1000, 2000 và 3000) có thể phát sinh. Bảng 4.6 thống kê sai lệch trung bình bước nhảy biên độ và sai lệch độ dốc tại các điểm nối so với dạng sóng liên tục gốc trên {n_test_ctx} ngữ cảnh kiểm thử:")

    add_paragraph_styled(doc, "Sai lệch biên độ có đơn vị biên độ PPG tùy ý; sai lệch độ dốc là chênh lệch sai phân một mẫu, chưa nhân tần số lấy mẫu để thành đạo hàm theo giây. Ba điểm nối của mỗi ngữ cảnh được tổng hợp trong từng người bệnh, rồi lấy trung bình đều trên 10 người bệnh, tổng cộng 546 điểm nối.")
    bnd_headers = ["Phương pháp", "Tỷ số nén", "Sai lệch bước nhảy biên độ", "Sai lệch biến thiên độ dốc", "Nhận xét tính trơn"]
    bnd = res.get("evaluation", {}).get("boundary_analysis", res.get("boundary_analysis", {}))
    bnd_data = []
    bnd_methods = [
        ("Uncompressed", "1.00x", "Mốc so sánh gốc: sai lệch bằng 0 theo định nghĩa"),
        ("DCT-1D (8x)", "8.00x", "Méo biên độ cục bộ do cắt hệ số"),
        ("DCT-1D (16x)", "16.13x", "Sai lệch bước nhảy lớn hơn DCT 8× trong phép đo này"),
        ("Autoencoder-MSE (8x)", "8.00x", "Sai lệch bước nhảy nhỏ hơn DCT 8× trong phép đo này"),
        ("Autoencoder-MSE (16x)", "16.13x", "Sai lệch bước nhảy lớn hơn mức 8× trong phép đo này"),
        ("De xuat Day du (8x)", "8.00x", "Sai lệch biên được đo riêng; chưa đánh giá pha"),
        ("De xuat Day du (16x)", "16.13x", "Có sai lệch biên; chưa kiểm chứng hình thái lâm sàng")
    ]
    for m_k, cr_k, note_k in bnd_methods:
        d_name = METHOD_DISPLAY_NAMES.get(m_k, m_k)
        if m_k in bnd:
            amp_err = bnd[m_k]["amp_step_error_mean"]
            slope_err = bnd[m_k]["slope_error_mean"]
            bnd_data.append([d_name, cr_k, f"{amp_err:.4f}", f"{slope_err:.4f}", note_k])
        else:
            raise ValueError(f"Thieu thong ke diem noi cho {m_k}; khong thay bang 0")

    create_table_styled(doc, bnd_headers, bnd_data, col_widths=[1.8, 1.0, 1.6, 1.6, 1.8], bookmark_name="BM_TBL_4_6")
    add_caption_styled(doc, "Bảng 4.6: Phân tích sai lệch biên độ và độ dốc tại ba điểm nối khối trong ngữ cảnh 32 giây")
    add_paragraph_styled(doc, "Bảng 4.6 định lượng sai lệch biên độ và độ dốc tại các điểm nối khối. Các giá trị này giúp nhận diện nguy cơ xuất hiện gián đoạn nhân tạo; riêng phép đo hiện tại chưa đủ để khẳng định hoàn toàn rằng mọi đỉnh giả đã được loại bỏ.")
    
    # -------------------------------------------------------------------------
    # CHƯƠNG 5
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "CHƯƠNG 5. BÀN LUẬN VÀ PHÂN TÍCH CHUYÊN SÂU", level=1)
    
    add_heading_styled(doc, "5.1. Đối chiếu giữa mục tiêu đề cương và kết quả thực nghiệm", level=2)
    add_paragraph_styled(doc, "Bảng 5.1 trình bày sự đối chiếu trung thực giữa các mục tiêu đề ra ban đầu trong đề cương và các kết quả thực nghiệm đo đạc thực tế:")
    
    comp_headers = ["Chỉ số đánh giá", "Mục tiêu ban đầu", "Kết quả mức 8x", "Kết quả mức 16.1x", "Kết luận đánh giá"]
    comp_data = [
        ["Độ méo PRD", "Mốc tham khảo 6% / 9%", f"{p8['prd_mean']:.2f}%", f"{p16['prd_mean']:.2f}%", "Không chuyển ngưỡng PRD sang PRDc; đây là mốc kỳ vọng, không phải ngưỡng lâm sàng"],
        ["Sai số nhịp tim MAE-HR", "<= 2.5 bpm", f"{p8_hr_inter:.2f} bpm", f"{p16_hr_inter:.2f} bpm", f"Mức 16x được đối chiếu với tín hiệu gốc ({uncomp_hr_inter:.2f} bpm)"],
        ["Sai số nhịp thở MAE-RR", "<= 1.5 brpm", f"{p8_rr_inter:.2f} brpm", f"{p16_rr_inter:.2f} brpm", f"8x: {'đạt' if p8_rr_inter <= 1.5 else 'chưa đạt'}; 16x: {'đạt' if p16_rr_inter <= 1.5 else 'chưa đạt'} mốc tham khảo trên tập giao"],
        ["Độ bao phủ nhịp thở", "Không đặt thấp", f"{p8_cov:.1f}%", f"{p16_cov:.1f}%", f"Tỷ lệ cửa sổ hợp lệ còn thấp; tập giao có đủ {inter_info['rr_intersection_patients']}/{n_test_subs} người bệnh"],
        ["Độ trễ xử lý khối 8 giây", "<= 20.0 ms", f"{bench_8x['total_median_ms']:.2f} ms", f"{bench_16x['total_median_ms']:.2f} ms", "Chỉ đo CPU tham chiếu, chưa đo vi điều khiển"]
    ]
    create_table_styled(doc, comp_headers, comp_data, col_widths=[1.6, 1.2, 1.2, 1.2, 1.8], bookmark_name="BM_TBL_5_1")
    add_caption_styled(doc, "Bảng 5.1: Bảng đối chiếu giữa mục tiêu ban đầu và kết quả thực nghiệm đo đạc thực tế")
    
    add_paragraph_styled(doc, f"Bảng đối chiếu trình bày riêng PRD và các chỉ số sinh lý để giữ đúng định nghĩa mục tiêu. PRDc của hai mức nén là {p8_prdc:.2f}% và {p16_prdc:.2f}%; MAE-RR là {p8_rr_inter:.2f} và {p16_rr_inter:.2f} nhịp thở/phút. Những giá trị này cần được đọc cùng độ bao phủ và số bệnh nhân hợp lệ. Với nhịp tim ở mức 16,1 lần, khoảng tin cậy của chênh lệch MAE so với tín hiệu chưa nén là từ {p16_delta_hr_ci[0]:.2f} đến {p16_delta_hr_ci[1]:.2f} nhịp/phút. {hr_difference_text}")
    
    add_heading_styled(doc, "5.2. Phân tích giới hạn của bộ đọc nhịp thở", level=2)
    add_paragraph_styled(doc, f"Phân tích bổ sung sau thí nghiệm giữ nguyên mô hình và bộ đọc, không dùng tập kiểm thử để chọn lại tham số. Trên {label_audit['rr']['valid']} ngữ cảnh kiểm thử có nhãn nhịp thở hợp lệ, một số ngữ cảnh bị từ chối do RIIV và RIAV chênh nhau quá 3,0 nhịp thở/phút hoặc không đủ thời lượng hữu dụng 24 giây. Vì tín hiệu không nén cũng có sai số sinh lý, không thể quy toàn bộ hạn chế cho riêng phép nén.")
    add_paragraph_styled(doc, f"So sánh ghép cặp dùng {inter_info['rr_intersection_contexts']} ngữ cảnh chung trên {inter_info['total_test_contexts']} ngữ cảnh kiểm thử, sau đó lấy trung bình đều theo đối tượng người bệnh. Bảng chi tiết theo từng đối tượng và trạng thái đặc trưng được lưu trong hồ sơ phân tích; không dùng chúng để điều chỉnh ngưỡng theo tập kiểm thử.")
    
    add_heading_styled(doc, "5.3. Giới hạn của phép đo tham chiếu trên CPU", level=2)
    add_paragraph_styled(doc, f"Mặc dù kết quả đo đạc độ trễ trên CPU máy tính đạt {bench['total_median_ms']:.2f} mili-giây (nhỏ hơn nhiều so với chu kỳ 8.000 mili-giây), kết quả này mang tính chất kiểm tra độ phức tạp thuật toán tham chiếu, chưa thể coi là bằng chứng cho khả năng tiết kiệm năng lượng thực tế trên vi điều khiển. Việc triển khai thực tế trên chip ARM Cortex-M4 đòi hỏi phải chuyển đổi mô hình sang định dạng TensorFlow Lite for Microcontrollers hoặc CMSIS-NN và đo dòng tiêu thụ qua thiết bị đo chuyên dụng.")
    
    add_heading_styled(doc, "5.4. Các trường hợp suy biến chất lượng tín hiệu", level=2)
    add_paragraph_styled(doc, "Mã ghi nhận lý do bộ đọc thất bại như thiếu đỉnh, mật độ nhịp không đủ và không đạt đồng thuận. Những trạng thái này không xác định nguyên nhân lâm sàng. Chưa có phép đo chuyển động hoặc tưới máu để quy kết lỗi cho chuyển động, huyết áp thấp hay co mạch.")
    
    # -------------------------------------------------------------------------
    # CHƯƠNG 6
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "CHƯƠNG 6. KẾT LUẬN VÀ HƯỚNG PHÁT TRIỂN", level=1)
    
    add_heading_styled(doc, "6.1. Kết luận tổng kết đề tài", level=2)
    add_paragraph_styled(doc, f"Quy trình đã được triển khai và đánh giá trên máy tính với tỷ số nén thực tế 8,000 và 16,129 lần. PRDc tương ứng là {p8_prdc:.2f}% và {p16_prdc:.2f}%; MAE-RR là {p8_rr_inter:.2f} và {p16_rr_inter:.2f} nhịp thở/phút, chưa đạt mốc 1,5. Độ bao phủ RR là {p8_cov:.1f}% và {p16_cov:.1f}%. Kết quả không thay thế kiểm chứng trên phần cứng hoặc đánh giá lâm sàng.")
    
    add_heading_styled(doc, "6.2. Hướng mở rộng nghiên cứu trong tương lai", level=2)
    add_paragraph_styled(doc, "Nhằm khắc phục các tồn tại đã chỉ ra, các hướng phát triển tiếp theo bao gồm:")
    add_paragraph_styled(doc, "1. Nghiên cứu tích hợp tín hiệu gia tốc kế ba trục vào mô hình nén tại biên để lọc thích nghi nhiễu chuyển động thể chất.")
    add_paragraph_styled(doc, "2. Cải tiến hàm mất mát phổ bằng cách áp dụng biến đổi Wavelet liên tục hoặc phân tích Hilbert-Huang để theo dõi biến thiên hô hấp phi dừng tốt hơn biến đổi Fourier.")
    add_paragraph_styled(doc, "3. Chuyển đổi mô hình bộ mã hóa sang CMSIS-NN và triển khai thực tế trên vi điều khiển STM32 hoặc nRF52840, đo lường trực tiếp dòng điện tiêu thụ và thời lượng pin.")
    
    # -------------------------------------------------------------------------
    # TÀI LIỆU THAM KHẢO (8 TÀI LIỆU CHUẨN IEEE CÓ DOI XÁC THỰC)
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "TÀI LIỆU THAM KHẢO", level=1)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    
    refs = [
        "[1] M. A. F. Pimentel, A. E. W. Johnson, P. H. Charlton, D. Birrenkott, P. J. Watkinson, L. Tarassenko, and D. A. Clifton, \"Toward a robust estimation of respiratory rate from pulse oximeters,\" IEEE Transactions on Biomedical Engineering, vol. 64, no. 8, pp. 1914–1923, Aug. 2017, doi: 10.1109/TBME.2016.2613124.",
        "[2] P. H. Charlton, T. Bonnici, L. Tarassenko, D. A. Clifton, R. Beale, and P. J. Watkinson, \"An assessment of algorithms to estimate respiratory rate from the electrocardiogram and photoplethysmogram,\" Physiological Measurement, vol. 37, no. 4, pp. 610–626, Apr. 2016, doi: 10.1088/0967-3334/37/4/610.",
        "[3] A. L. Goldberger, L. A. N. Amaral, L. Glass, J. M. Hausdorff, P. C. Ivanov, R. G. Mark, J. E. Mietus, G. B. Moody, C. K. Peng, and H. E. Stanley, \"PhysioBank, PhysioToolkit, and PhysioNet: Components of a new research resource for complex physiologic signals,\" Circulation, vol. 101, no. 23, pp. e215–e220, Jun. 2000, doi: 10.1161/01.CIR.101.23.e215. (BIDMC Dataset DOI: 10.13026/C2208R).",
        "[4] Y. Bengio, N. Léonard, and C. Courville, \"Estimating or propagating gradients through stochastic neurons for conditional computation,\" arXiv preprint arXiv:1308.3432, 2013.",
        "[5] J. Allen, \"Photoplethysmography and its application in clinical physiological measurement,\" Physiological Measurement, vol. 28, no. 3, pp. R1–R39, Feb. 2007, doi: 10.1088/0967-3334/28/3/R01.",
        "[6] W. Karlen, S. Raman, J. M. Ansermino, and G. A. Dumont, \"Multiparameter respiratory rate estimation from the photoplethysmogram,\" IEEE Transactions on Biomedical Engineering, vol. 60, no. 7, pp. 1946–1953, Jul. 2013, doi: 10.1109/TBME.2013.2246160.",
        "[7] M. Elgendi, \"On the analysis of fingertip photoplethysmogram signals,\" Current Cardiology Reviews, vol. 8, no. 1, pp. 14–25, Feb. 2012, doi: 10.2174/157340312801215782.",
        "[8] T. Tamura, Y. Maeda, M. Sekine, and M. Yoshida, \"Wearable photoplethysmographic sensors—past and present,\" Electronics, vol. 3, no. 2, pp. 282–302, Apr. 2014, doi: 10.3390/electronics3020282."
    ]
    for r_item in refs:
        p_r = doc.add_paragraph()
        p_r.paragraph_format.space_before = Pt(2)
        p_r.paragraph_format.space_after = Pt(2.5)
        p_r.paragraph_format.line_spacing = 1.2
        p_r.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r_run = p_r.add_run(r_item)
        r_run.font.name = 'Times New Roman'
        r_run.font.size = Pt(11)
        r_run.font.color.rgb = RGBColor(0, 0, 0)
        
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    
    # -------------------------------------------------------------------------
    # PHỤ LỤC
    # -------------------------------------------------------------------------
    add_heading_styled(doc, "PHỤ LỤC", level=1)
    
    add_heading_styled(doc, "Phụ lục A: Cấu trúc thư mục mã nguồn triển khai", level=2)
    add_paragraph_styled(doc, "Mã nguồn được chia thành các thành phần có chức năng độc lập:", space_after=2)
    
    app_a_items = [
        ("1. configs/c5.yaml:", " Cấu hình tham số hệ thống, mức nén và siêu tham số huấn luyện."),
        ("2. data/raw_bidmc/:", " Thư mục chứa 53 hồ sơ tín hiệu gốc BIDMC kèm nhãn chuẩn y tế."),
        ("3. models/autoencoder.py:", " Bộ mã hóa, bộ giải mã tích chập một chiều và lượng tử STE."),
        ("4. models/dct_baseline.py:", " Mô hình đối chứng nén biến đổi cosin rời rạc DCT-1D."),
        ("5. utils/packet_codec.py:", " Đóng gói đầu 18 byte, tải trọng, CRC-16 và theo dõi số thứ tự."),
        ("6. utils/spectral_loss.py:", " Hàm mất mát đa thang phổ thời gian và tần số."),
        ("7. utils/estimators.py:", " Ước lượng nhịp tim và nhịp thở bằng nội suy tuyến tính, phổ Welch và quy tắc đồng thuận."),
        ("8. train.py & evaluate.py:", " Kịch bản huấn luyện mô hình và đánh giá các chỉ số sinh học."),
        ("9. benchmark_edge.py:", " Đo đạc tài nguyên tham chiếu trên CPU và kiểm thử lỗi CRC-16."),
        ("10. results_per_context.csv:", f" Kết quả chi tiết ước lượng nhịp thở và nhãn tham chiếu cho {n_test_ctx} ngữ cảnh 32 giây."),
        ("11. run_experiments.py:", " Kịch bản giao diện chạy thống nhất toàn diện các giai đoạn.")
    ]
    for tag_item, desc_item in app_a_items:
        p_item = doc.add_paragraph()
        p_item.paragraph_format.space_before = Pt(0)
        p_item.paragraph_format.space_after = Pt(1.5)
        p_item.paragraph_format.line_spacing = 1.15
        p_item.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r_tag = p_item.add_run(tag_item)
        r_tag.font.name = 'Times New Roman'
        r_tag.font.size = Pt(11)
        r_tag.font.bold = True
        r_tag.font.color.rgb = RGBColor(0, 0, 0)
        r_desc = p_item.add_run(desc_item)
        r_desc.font.name = 'Times New Roman'
        r_desc.font.size = Pt(11)
        r_desc.font.color.rgb = RGBColor(0, 0, 0)
        
    add_heading_styled(doc, "Phụ lục B: Hướng dẫn lệnh tái hiện kết quả thực nghiệm", level=2)
    add_paragraph_styled(doc, "Các lệnh sau kiểm tra và tái hiện đánh giá từ mô hình đã lưu, không huấn luyện lại. Môi trường phải có đủ thư viện; xem README để cài đặt và dựng báo cáo:", space_after=2)
    
    app_b_items = [
        ("1.", " Kiểm thử: python -m pytest -q --basetemp reports/pytest_demo_temp"),
        ("2.", " Trình diễn: python demo_pipeline.py --level 8x; đổi thành 16x để chạy mức còn lại."),
        ("3.", " Tái lập trong thư mục sạch: python verify_delivery.py"),
        ("4.", " Dựng báo cáo: python run_experiments.py --stage report --device cpu")
    ]
    for tag_b, desc_b in app_b_items:
        p_item = doc.add_paragraph()
        p_item.paragraph_format.space_before = Pt(0)
        p_item.paragraph_format.space_after = Pt(1.5)
        p_item.paragraph_format.line_spacing = 1.15
        p_item.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r_tag = p_item.add_run(tag_b)
        r_tag.font.name = 'Times New Roman'
        r_tag.font.size = Pt(11)
        r_tag.font.bold = True
        r_tag.font.color.rgb = RGBColor(0, 0, 0)
        r_desc = p_item.add_run(desc_b)
        r_desc.font.name = 'Times New Roman'
        r_desc.font.size = Pt(11)
        r_desc.font.color.rgb = RGBColor(0, 0, 0)
    
    for paragraph in doc.paragraphs:
        paragraph.paragraph_format.line_spacing = 1.2
        if not paragraph.text.strip() and not paragraph._p.xpath('.//w:sectPr | .//w:br | .//w:drawing'):
            paragraph.paragraph_format.keep_with_next = True
    update_formula_references(doc)
    doc.save(OUTPUT_DOCX_PATH)
    print(f"\n-> DA XUAT THANH CONG BAO CAO HOAN THIEN TAI: {OUTPUT_DOCX_PATH}")
    return OUTPUT_DOCX_PATH

if __name__ == "__main__":
    generate_report()
