"""Run only as an isolated local desktop conversion worker."""
import argparse
from pathlib import Path


def convert(source, destination):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = document = None
    try:
        if source.suffix.lower()=='.docx':
            app = win32com.client.DispatchEx('Word.Application')
            app.Visible = False
            app.DisplayAlerts = 0
            document = app.Documents.Open(str(source), ReadOnly=True, AddToRecentFiles=False)
            document.ExportAsFixedFormat(str(destination), 17, OpenAfterExport=False)
        elif source.suffix.lower()=='.pptx':
            app = win32com.client.DispatchEx('PowerPoint.Application')
            document = app.Presentations.Open(str(source), ReadOnly=True, Untitled=False, WithWindow=False)
            document.SaveAs(str(destination), 32)
        else:
            raise ValueError('Unsupported preview type')
    finally:
        if document is not None:
            if source.suffix.lower()=='.docx':
                document.Close(SaveChanges=False)
            else:
                document.Close()
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args = parser.parse_args()
    convert(args.source.resolve(),args.destination.resolve())
