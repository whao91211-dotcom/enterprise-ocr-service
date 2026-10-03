"""PDFium calls must be serialized within each application process."""
from threading import Lock

PDF_LOCK=Lock()
