import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCX_PATH = os.path.join(BASE_DIR, "Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.docx")
PDF_PATH = os.path.join(BASE_DIR, "Bao_Cao_Tieu_Luan_Cuoi_Khoa_C5_DangGiaHuy_HoanThien.pdf")
PREVIEW_DIR = os.path.join(BASE_DIR, "reports", "qa_pages_" + datetime.now().strftime('%Y%m%d_%H%M%S'))

def update_docx_and_export_pdf(docx_path=DOCX_PATH, pdf_path=PDF_PATH):
    # Use an isolated Word instance; never terminate the user's Word sessions
    # or alter Office resiliency registry settings.
    
    import win32com.client
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = False
    
    try:
        abs_docx = os.path.abspath(docx_path)
        abs_pdf = os.path.abspath(pdf_path)
        print(f"Opening: {abs_docx}")
        doc = word.Documents.Open(abs_docx)
        
        # 1. Dam bao lap hang tieu de cho tat ca cac bang (Accessibility tblHeader)
        print("Enforcing heading format on all tables...")
        for i in range(1, doc.Tables.Count + 1):
            try:
                tbl = doc.Tables(i)
                tbl.Rows(1).HeadingFormat = True
            except Exception as e:
                print(f"Warning setting table {i} heading: {e}")
                
        # 2. Cap nhat AlternativeText cho cac hinh anh
        print("Checking inline shapes alternative text...")
        for i in range(1, doc.InlineShapes.Count + 1):
            try:
                s = doc.InlineShapes(i)
                if not s.AlternativeText:
                    s.AlternativeText = f"Hình ảnh kỹ thuật đề tài C5 số {i}"
            except Exception as e:
                print(f"Warning setting shape {i} alt text: {e}")
                
        # 3. Cap nhat Tables of Contents
        print("Updating Tables of Contents...")
        for i in range(1, doc.TablesOfContents.Count + 1):
            doc.TablesOfContents(i).Update()
            
        # 4. Cap nhat cac truong trong than van ban
        print("Updating body fields...")
        doc.Fields.Update()
        
        # 5. Cap nhat cac truong trong headers / footers
        for s in range(1, doc.Sections.Count + 1):
            sec = doc.Sections(s)
            for hf in [sec.Headers(1), sec.Footers(1), sec.Headers(2), sec.Footers(2)]:
                try:
                    hf.Range.Fields.Update()
                except Exception:
                    pass
                    
        # 6. Tinh toan thong ke so trang
        pages = doc.ComputeStatistics(2) # 2 = wdStatisticPages
        paragraphs = doc.ComputeStatistics(4) # 4 = wdStatisticParagraphs
        words = doc.ComputeStatistics(0) # 0 = wdStatisticWords
        print(f"\n===> THONG KE TAI LIEU WORD:")
        print(f"     So trang (Pages): {pages}")
        print(f"     So tu (Words): {words}")
        print(f"     So doan (Paragraphs): {paragraphs}")
        
        doc.Save()
        
        # 7. Xuat sang PDF
        print(f"\nExporting to PDF: {abs_pdf}")
        doc.SaveAs2(abs_pdf, FileFormat=17) # 17 = wdFormatPDF
        doc.Close()
        print("Document saved and exported to PDF successfully.")
        
    finally:
        word.Quit()
        
    # Page rendering is performed with the bundled PDFium runtime separately.
    if os.path.exists(abs_pdf):
        try:
            import pypdf
            pdf_doc = pypdf.PdfReader(abs_pdf)
            pdf_pages = len(pdf_doc.pages)
            print(f"===> XAC NHAN SO TRANG TEP PDF: {pdf_pages} TRANG")
            
            # Xoa toan bo anh preview cu
            os.makedirs(PREVIEW_DIR, exist_ok=True)
            
            # Render tung trang
            print('PDF exported; render all pages using bundled document_qa.py before delivery.')
        except Exception as e:
            print(f"Warning rendering PDF preview: {e}")
            
    return pages

if __name__ == "__main__":
    update_docx_and_export_pdf()
