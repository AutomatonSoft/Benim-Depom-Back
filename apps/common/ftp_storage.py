import ftplib
import posixpath
from contextlib import contextmanager
from contextvars import ContextVar
from tempfile import SpooledTemporaryFile
from urllib.parse import quote
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

_reused_ftp: ContextVar[object | None] = ContextVar(
    "ftp_media_reused_connection",
    default=None,
)


@deconstructible
class FTPMediaStorage(Storage):
    def __init__(self):
        self.host = settings.FTP_MEDIA_HOST
        self.port = settings.FTP_MEDIA_PORT
        self.username = settings.FTP_MEDIA_USERNAME
        self.password = settings.FTP_MEDIA_PASSWORD
        self.use_tls = settings.FTP_MEDIA_USE_TLS
        self.passive_mode = settings.FTP_MEDIA_PASSIVE_MODE
        self.timeout = settings.FTP_MEDIA_TIMEOUT_SECONDS

        self.remote_root = ("/" + settings.FTP_MEDIA_REMOTE_ROOT.strip("/")).rstrip("/")

        self.public_base_url = settings.FTP_MEDIA_PUBLIC_BASE_URL.rstrip("/")

    def _clean_name(self, name: str) -> str:
        normalized_name = posixpath.normpath(str(name).replace("\\", "/")).lstrip("/")

        if (
            not normalized_name
            or normalized_name in {".", ".."}
            or normalized_name.startswith("../")
        ):
            raise SuspiciousFileOperation(f"Invalid FTP file name: {name}")

        return normalized_name

    def _remote_path(self, name: str) -> str:
        return posixpath.join(
            self.remote_root,
            self._clean_name(name),
        )

    @contextmanager
    def reuse_connection(self):
        """Keep one FTP login for nested save/exists/delete in this request."""
        if _reused_ftp.get() is not None:
            yield
            return

        with self._open_connection() as ftp:
            token = _reused_ftp.set(ftp)
            try:
                yield
            finally:
                _reused_ftp.reset(token)

    @contextmanager
    def _open_connection(self):
        ftp_class = ftplib.FTP_TLS if self.use_tls else ftplib.FTP
        ftp = ftp_class(timeout=self.timeout)

        try:
            ftp.connect(self.host, self.port)
            ftp.login(self.username, self.password)

            if self.use_tls:
                ftp.prot_p()

            ftp.set_pasv(self.passive_mode)

            yield ftp
        except ftplib.all_errors as exc:
            raise OSError("FTP media storage operation failed.") from exc
        finally:
            try:
                ftp.quit()
            except ftplib.all_errors:
                try:
                    ftp.close()
                except ftplib.all_errors:
                    pass

    @contextmanager
    def _connection(self):
        reused = _reused_ftp.get()
        if reused is not None:
            yield reused
            return

        with self._open_connection() as ftp:
            yield ftp

    def _ensure_remote_directory(self, ftp, remote_path: str) -> None:
        directory = posixpath.dirname(remote_path)

        if directory in {"", "/"}:
            return

        current_path = ""

        for directory_name in directory.strip("/").split("/"):
            current_path = f"{current_path}/{directory_name}"

            try:
                ftp.mkd(current_path)
            except ftplib.error_perm:
                # Directory already exists.
                pass

    def _open(self, name: str, mode: str = "rb"):
        if mode != "rb":
            raise ValueError("FTP storage supports read-only binary mode.")

        temporary_file = SpooledTemporaryFile(
            max_size=5 * 1024 * 1024,
            mode="w+b",
        )

        with self._connection() as ftp:
            ftp.retrbinary(
                f"RETR {self._remote_path(name)}",
                temporary_file.write,
            )

        temporary_file.seek(0)

        return File(temporary_file, name=name)

    def _save(self, name: str, content) -> str:
        clean_name = self._clean_name(name)
        remote_path = self._remote_path(clean_name)
        temporary_remote_path = f"{remote_path}.uploading-{uuid4().hex}"

        if hasattr(content, "seek"):
            content.seek(0)

        with self._connection() as ftp:
            self._ensure_remote_directory(ftp, remote_path)

            try:
                ftp.storbinary(
                    f"STOR {temporary_remote_path}",
                    content,
                    blocksize=64 * 1024,
                )
                ftp.rename(temporary_remote_path, remote_path)
            except Exception:
                try:
                    ftp.delete(temporary_remote_path)
                except ftplib.all_errors:
                    pass

                raise

        return clean_name

    def delete(self, name: str) -> None:
        with self._connection() as ftp:
            try:
                ftp.delete(self._remote_path(name))
            except ftplib.error_perm as exc:
                if str(exc).startswith("550"):
                    return

                raise

    def exists(self, name: str) -> bool:
        with self._connection() as ftp:
            try:
                return ftp.size(self._remote_path(name)) is not None
            except ftplib.error_perm:
                return False

    def size(self, name: str) -> int:
        with self._connection() as ftp:
            size = ftp.size(self._remote_path(name))

        if size is None:
            raise OSError(f"Could not get FTP file size: {name}")

        return size

    def url(self, name: str) -> str:
        clean_name = self._clean_name(name)

        return f"{self.public_base_url}/{quote(clean_name, safe='/')}"
