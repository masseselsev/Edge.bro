"""Browsing inside a finished backup, without restoring it.

Someone deleted one file and wants it back. Restoring the archive would flatten
a working machine to get it, so these three endpoints let an operator list an
archive, read a file out of it, and download a file or folder — leaving the
node alone.

Split out of `routers/nodes_crud.py`, where 240 of its 850 lines were this and
had nothing to do with node CRUD. The URLs are unchanged: they stay under
`/api/nodes/history/...` because the frontend and every kiosk image already
built point at them, and a cosmetically nicer path is not worth an ISO rebuild.

Every command here runs with `--bypass-lock`. Read-only borg operations do not
need the repository lock, and taking it would make browsing an archive block —
or be blocked by — a running backup or the nightly prune. On a fleet where
something is almost always writing, requiring the lock would mean the file
browser rarely worked.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import models
import schemas
from auth import require_kiosk_or_admin
from core.borg_local import borg_kwargs, grant_workdir
from core.repo_usage import DEFAULT_REPO_PATH
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nodes/history", tags=["Archives"])

#: Preview cap. Large enough for any config file or log an operator would read
#: in a browser, small enough that a stray `borg extract` of a database dump
#: does not pull gigabytes into the API process's memory.
MAX_PREVIEW_BYTES = 500 * 1024

#: A whole-folder extract is bounded by wall clock rather than size, because
#: size is not knowable before extracting. Three minutes is generous for the
#: config directories this is used on and short enough that a mistaken click on
#: /var does not tie up a worker indefinitely.
FOLDER_EXTRACT_TIMEOUT = 180


def _archive_context(db: Session, history_id: int) -> Tuple[models.BackupHistory, str, Dict[str, str]]:
    """Resolve a history row to its archive, repository path and borg env.

    The same eight lines opened all three endpoints. Raises 404 for both an
    unknown history row and a missing repository, which are indistinguishable
    to the caller and equally final.
    """
    history = db.query(models.BackupHistory).filter(models.BackupHistory.id == history_id).first()
    if not history:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup history record not found.")

    repo_path = DEFAULT_REPO_PATH
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borg repository does not exist.")

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
    return history, repo_path, env


def _clean_relative_path(path: str) -> str:
    """Archive paths are stored relative; a leading slash finds nothing."""
    clean_path = path.strip().lstrip("/")
    if not clean_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path.")
    return clean_path


@router.get("/{history_id}/files", response_model=schemas.ArchiveFileListResponse)
def get_archive_files(
    history_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin),
):
    """List everything inside one archive."""
    history, repo_path, env = _archive_context(db, history_id)

    try:
        # --json-lines rather than --json: borg streams one object per file, so
        # a full-system archive does not have to be buffered as a single JSON
        # document before anything can be parsed.
        cmd = ["borg", "list", "--bypass-lock", "--json-lines", f"{repo_path}::{history.archive_name}"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30, **borg_kwargs(repo_path, env))
        if res.returncode != 0:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Borg list failed: {res.stderr.strip()}")

        file_items = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                mode = item.get("mode", "")
                file_items.append(schemas.ArchiveFileInfo(
                    path=item.get("path", ""),
                    size=item.get("size", 0),
                    mtime=item.get("mtime"),
                    mode=mode,
                    # borg reports the type in the mode string's first
                    # character, the way ls does.
                    is_dir=mode.startswith("d") if mode else False,
                ))
            except json.JSONDecodeError:
                # One malformed line must not lose the rest of the listing.
                continue

        return schemas.ArchiveFileListResponse(archive_name=history.archive_name, files=file_items)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Listing archive files timed out.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/{history_id}/file-content", response_model=schemas.ArchiveFileContentResponse)
def get_archive_file_content(
    history_id: int,
    path: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin),
):
    """Read one file out of an archive as text, for previewing in the browser.

    Refuses rather than mangles: too large, or containing a NUL byte, and the
    caller gets a reason instead of a screenful of garbage. All three refusals
    are 200 responses with `is_text=False` — the request succeeded, and the
    answer is that the file is not previewable.
    """
    history, repo_path, env = _archive_context(db, history_id)
    clean_path = _clean_relative_path(path)

    try:
        cmd = ["borg", "extract", "--bypass-lock", "--stdout", f"{repo_path}::{history.archive_name}", clean_path]
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **borg_kwargs(repo_path, env))

        # One byte past the cap, so "exactly at the limit" and "larger than the
        # limit" can be told apart without reading the whole file.
        raw_bytes = proc.stdout.read(MAX_PREVIEW_BYTES + 1)
        proc.stdout.close()
        proc.stderr.close()
        proc.wait(timeout=10)

        if len(raw_bytes) > MAX_PREVIEW_BYTES:
            return schemas.ArchiveFileContentResponse(
                path=clean_path, is_text=False, size=len(raw_bytes), content=None,
                message="File exceeds maximum preview size of 500 KB.",
            )

        # The same heuristic `file` and git use: no NUL byte, treat as text.
        if b"\x00" in raw_bytes:
            return schemas.ArchiveFileContentResponse(
                path=clean_path, is_text=False, size=len(raw_bytes), content=None,
                message="Binary file cannot be displayed as text.",
            )

        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # latin-1 decodes any byte sequence, so this branch is the last
            # stop: it renders a legacy-encoded config readably rather than
            # refusing it. It cannot itself fail on well-formed input.
            try:
                text_content = raw_bytes.decode("latin-1")
            except Exception:
                return schemas.ArchiveFileContentResponse(
                    path=clean_path, is_text=False, size=len(raw_bytes), content=None,
                    message="File encoding is not readable as text.",
                )

        return schemas.ArchiveFileContentResponse(
            path=clean_path, is_text=True, size=len(raw_bytes), content=text_content,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Extracting file content timed out.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


def _download_folder_as_zip(history: models.BackupHistory, repo_path: str, env: Dict[str, str], clean_path: str):
    """Extract a folder to scratch, zip it, and stream the zip out.

    Not streamed directly out of borg, because a zip's central directory is
    written at the end and cannot be produced until every member is known.

    Scratch space lives next to the repository rather than in the container's
    own /tmp: extracting a folder can be as large as the folder was, and
    repository storage is what deployments size for that — /tmp is typically
    the small root disk.
    """
    tmp_root = os.path.join(os.path.dirname(repo_path), "tmp")
    os.makedirs(tmp_root, exist_ok=True)
    os.chmod(tmp_root, 0o755)  # traversable regardless of which uid borg_kwargs picks below
    temp_dir = tempfile.mkdtemp(dir=tmp_root)
    zip_path = os.path.join(temp_dir, "archive.zip")
    # borg extracts into its working directory, so hand the temp dir to
    # whichever identity we are about to run it as.
    grant_workdir(temp_dir, repo_path)

    try:
        cmd = ["borg", "extract", "--bypass-lock", f"{repo_path}::{history.archive_name}", clean_path]
        res = subprocess.run(
            cmd, env=env, cwd=temp_dir, capture_output=True, text=True,
            timeout=FOLDER_EXTRACT_TIMEOUT, **borg_kwargs(repo_path, env),
        )
        if res.returncode != 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Borg folder extraction failed: {res.stderr.strip()}",
            )

        extracted_target = os.path.join(temp_dir, clean_path)
        folder_name = os.path.basename(clean_path.rstrip("/")) or "folder"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(extracted_target):
                if os.path.isdir(extracted_target):
                    for root, _, files in os.walk(extracted_target):
                        for file in files:
                            full_file_path = os.path.join(root, file)
                            # Relative to the folder's parent, so the zip opens
                            # as one named directory rather than a loose pile.
                            arcname = os.path.relpath(full_file_path, os.path.dirname(extracted_target))
                            zf.write(full_file_path, arcname)
                else:
                    zf.write(extracted_target, os.path.basename(extracted_target))

        def iter_zip():
            try:
                with open(zip_path, "rb") as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                # Cleanup belongs in the generator, not after the return: the
                # response has not been sent when this function exits, and
                # removing the tree here would delete the file being streamed.
                shutil.rmtree(temp_dir, ignore_errors=True)

        encoded_filename = f"{folder_name}.zip".replace('"', '\\"')
        return StreamingResponse(
            iter_zip(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{encoded_filename}"'},
        )
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/{history_id}/download-file")
def download_archive_file(
    history_id: int,
    path: str,
    is_dir: bool = False,
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin),
):
    """Download one file, or a folder as a zip.

    A single file streams straight from borg's stdout and never lands on disk,
    so its size is unbounded. A folder cannot work that way — see
    `_download_folder_as_zip`.
    """
    history, repo_path, env = _archive_context(db, history_id)
    clean_path = _clean_relative_path(path)

    if is_dir:
        return _download_folder_as_zip(history, repo_path, env, clean_path)

    cmd = ["borg", "extract", "--bypass-lock", "--stdout", f"{repo_path}::{history.archive_name}", clean_path]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **borg_kwargs(repo_path, env))

    def iterfile():
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            # Reaped here rather than at return: the process is still producing
            # output when this endpoint's own frame has gone.
            proc.stdout.close()
            proc.stderr.close()
            proc.wait()

    filename = os.path.basename(clean_path) or "download"
    encoded_filename = filename.replace('"', '\\"')

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{encoded_filename}"'},
    )
