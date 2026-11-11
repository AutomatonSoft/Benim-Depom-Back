from itertools import islice
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation

STAGING_DIRECTORY = "ebay-bulk-end"


def staging_root():
    root = Path(settings.PRODUCT_IMAGE_STAGING_ROOT).expanduser().resolve()
    app_root = root / STAGING_DIRECTORY
    app_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if app_root.is_symlink():
        raise SuspiciousFileOperation(
            "eBay report staging directory cannot be a symlink."
        )
    app_root.chmod(0o700)
    return root, app_root


def report_path(snapshot_id, import_generation, owner_token):
    UUID(str(snapshot_id))
    UUID(str(owner_token))
    _, app_root = staging_root()
    directory = app_root / str(snapshot_id)
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise SuspiciousFileOperation(
            "eBay snapshot staging directory cannot be a symlink."
        )
    directory.chmod(0o700)
    return directory / f"{int(import_generation)}-{str(owner_token)}.csv"


def relative_report_path(path):
    root, _ = staging_root()
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise SuspiciousFileOperation(
            "eBay report path is outside its staging root."
        ) from error


def resolve_report_path(name):
    if not isinstance(name, str) or not name:
        raise FileNotFoundError("No staged eBay report was recorded.")
    root, _ = staging_root()
    raw_candidate = root / name
    try:
        raw_candidate.relative_to(root / STAGING_DIRECTORY)
    except ValueError as error:
        raise SuspiciousFileOperation(
            "eBay report path is outside its staging directory."
        ) from error
    if raw_candidate.is_symlink():
        raise SuspiciousFileOperation("Staged eBay report cannot be a symlink.")
    candidate = raw_candidate.resolve(strict=True)
    try:
        candidate.relative_to(root / STAGING_DIRECTORY)
    except ValueError as error:
        raise SuspiciousFileOperation(
            "eBay report path is outside its staging directory."
        ) from error
    if candidate.is_symlink() or not candidate.is_file():
        raise FileNotFoundError("Staged eBay report is unavailable.")
    return candidate


def resolve_report_target(name):
    if not isinstance(name, str) or not name:
        raise FileNotFoundError("No staged eBay report path was recorded.")
    root, _ = staging_root()
    raw_candidate = root / name
    try:
        raw_candidate.relative_to(root / STAGING_DIRECTORY)
    except ValueError as error:
        raise SuspiciousFileOperation(
            "eBay report path is outside its staging directory."
        ) from error
    if raw_candidate.is_symlink():
        raise SuspiciousFileOperation("Staged eBay report cannot be a symlink.")
    candidate = raw_candidate.resolve(strict=False)
    try:
        candidate.relative_to(root / STAGING_DIRECTORY)
    except ValueError as error:
        raise SuspiciousFileOperation(
            "eBay report path is outside its staging directory."
        ) from error
    return candidate


def delete_report_path(name):
    if not name:
        return False
    root, _ = staging_root()
    candidate = root / name
    try:
        candidate.relative_to(root / STAGING_DIRECTORY)
    except ValueError:
        return False
    if candidate.is_symlink():
        return False
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root / STAGING_DIRECTORY)
    except (OSError, ValueError):
        return False
    if resolved.is_symlink() or not resolved.is_file():
        return False
    resolved.unlink()
    return True


def delete_snapshot_staging_files(snapshot_id, *, limit=100):
    UUID(str(snapshot_id))
    _, app_root = staging_root()
    directory = app_root / str(snapshot_id)
    if directory.is_symlink() or not directory.is_dir():
        return False
    entries = list(islice(directory.iterdir(), limit + 1))
    for entry in entries[:limit]:
        if not entry.is_symlink() and entry.is_file():
            entry.unlink()
    if len(entries) > limit:
        return True
    try:
        directory.rmdir()
    except OSError:
        return any(
            not entry.is_symlink() and entry.is_file() for entry in directory.iterdir()
        )
    return False
