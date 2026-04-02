"""File upload endpoint — accepts user documents for RAG ingestion."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, UploadFile

log = structlog.get_logger(__name__)
router = APIRouter()

# Ephemeral upload directory (cleaned up after analysis)
UPLOAD_DIR = Path("/tmp/agent_invest_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed file extensions
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".csv", ".md", ".html", ".htm"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("")
async def upload_file(file: UploadFile):
    """Upload a file for RAG ingestion.

    Accepts: TXT, PDF, CSV, MD, HTML (max 10MB)
    Returns: { file_id, filename, size_bytes, path }
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {ext} not supported. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read content with size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    # Save to temp directory with unique ID
    file_id = str(uuid.uuid4())[:12]
    safe_name = f"{file_id}_{file.filename}"
    file_path = UPLOAD_DIR / safe_name
    file_path.write_bytes(content)

    log.info("file_uploaded", file_id=file_id, filename=file.filename, size=len(content))

    return {
        "file_id": file_id,
        "filename": file.filename,
        "size_bytes": len(content),
        "path": str(file_path),
    }


def get_upload_path(file_id: str) -> str | None:
    """Look up the file path for a given file_id."""
    for f in UPLOAD_DIR.iterdir():
        if f.name.startswith(file_id):
            return str(f)
    return None


def cleanup_uploads(file_ids: list[str]) -> None:
    """Remove uploaded files after analysis is complete."""
    for file_id in file_ids:
        path = get_upload_path(file_id)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
