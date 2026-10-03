"""Run only as an isolated local desktop conversion worker."""
import argparse
from pathlib import Path


def claim_process(name,prior,marker):
    from services.office_process import existing_office,register_owned
    created=existing_office(name)-prior
    if len(created)!=1:
        raise RuntimeError('Cannot establish exclusive Office ownership')
    register_owned(created.pop(),name,prior,marker)


def convert(source, destination, marker):
    import pythoncom
    import win32com.client
    from services.office_process import existing_office
    name='winword.exe' if source.suffix.lower()=='.docx' else 'powerpnt.exe'
    prior=existing_office(name)
    # PowerPoint is a MultiUse COM server; never borrow or quit a user's instance.
    if name=='powerpnt.exe' and prior:
        raise RuntimeError('Close existing PowerPoint before requesting a local preview')
    pythoncom.CoInitialize()
    app = document = None
    owned=False
    try:
        if source.suffix.lower()=='.docx':
            app = win32com.client.DispatchEx('Word.Application')
            claim_process(name,prior,marker)
            owned=True
            app.Visible = False
            app.DisplayAlerts = 0
            document = app.Documents.Open(str(source), ReadOnly=True, AddToRecentFiles=False)
            document.ExportAsFixedFormat(str(destination), 17, OpenAfterExport=False)
        elif source.suffix.lower()=='.pptx':
            app = win32com.client.DispatchEx('PowerPoint.Application')
            claim_process(name,prior,marker)
            owned=True
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
        if app is not None and owned:
            app.Quit()
        pythoncom.CoUninitialize()


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    parser.add_argument('marker',type=Path)
    args = parser.parse_args()
    convert(args.source.resolve(),args.destination.resolve(),args.marker.resolve())
