"""Render synthetic tool reports with isolated Word COM and PDFium for visual QA."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    sources = [Path(p) for p in manifest['artifacts'] if p.endswith('.docx')]
    if not sources:
        raise RuntimeError('No generated reports to render')
    import pythoncom
    import win32com.client
    import pypdfium2
    pythoncom.CoInitialize()
    word = None
    result = {'renderer': 'Word COM PDF export + PDFium', 'reports': []}
    try:
        word = win32com.client.DispatchEx('Word.Application')
        word.Visible = False
        word.DisplayAlerts = 0
        for source in sources:
            pdf_path = source.with_suffix('.pdf')
            document = word.Documents.Open(str(source.resolve()), ReadOnly=True, AddToRecentFiles=False)
            try:
                document.ExportAsFixedFormat(str(pdf_path.resolve()), 17, OpenAfterExport=False)
            finally:
                document.Close(SaveChanges=False)
            pdf = pypdfium2.PdfDocument(str(pdf_path))
            pages = []
            try:
                for index in range(len(pdf)):
                    page = pdf[index]
                    bitmap = page.render(scale=1.3)
                    image_path = source.with_name(source.stem+f'_page_{index+1}.png')
                    bitmap.to_pil().save(image_path)
                    pages.append(str(image_path))
                    bitmap.close()
                    page.close()
            finally:
                pdf.close()
            result['reports'].append({'source': str(source), 'pdf': str(pdf_path), 'page_images': pages})
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
