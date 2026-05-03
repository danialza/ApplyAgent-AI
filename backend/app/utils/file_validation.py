"""Upload validation: extension, mime, size."""
from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_PROFILE_DOC_EXTENSIONS = {".pdf", ".docx", ".txt"}
ALLOWED_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    # Some browsers send generic types; allow octet-stream when extension is OK.
    "application/octet-stream",
}
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


def get_extension(filename: str) -> str:
    """Return lowercased extension, including the leading dot."""
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


def validate_upload(file: UploadFile, size: int) -> str:
    """Raise HTTPException on invalid file. Returns the validated extension."""
    ext = get_extension(file.filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )
    if file.content_type and file.content_type not in ALLOWED_MIMES:
        # Don't reject solely on mime; many clients lie. Extension already validated.
        pass
    if size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file uploaded.",
        )
    if size > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    return ext
