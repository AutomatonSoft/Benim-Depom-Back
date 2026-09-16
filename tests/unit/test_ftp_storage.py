import ftplib
from io import BytesIO

import pytest
from django.core.exceptions import SuspiciousFileOperation
from django.test import override_settings

from apps.common.ftp_storage import FTPMediaStorage


@pytest.mark.unit
@override_settings(
    FTP_MEDIA_HOST="ftp.example",
    FTP_MEDIA_PORT=21,
    FTP_MEDIA_USERNAME="user",
    FTP_MEDIA_PASSWORD="password",
    FTP_MEDIA_USE_TLS=True,
    FTP_MEDIA_PASSIVE_MODE=True,
    FTP_MEDIA_TIMEOUT_SECONDS=5,
    FTP_MEDIA_REMOTE_ROOT="api-media",
    FTP_MEDIA_PUBLIC_BASE_URL="https://cdn.example/api-media",
)
def test_ftp_storage_rejects_path_traversal_and_builds_public_url():
    storage = FTPMediaStorage()

    assert (
        storage._remote_path("products/1/photo name.jpg")
        == "/api-media/products/1/photo name.jpg"
    )
    assert (
        storage.url("products/1/photo name.jpg")
        == "https://cdn.example/api-media/products/1/photo%20name.jpg"
    )

    with pytest.raises(SuspiciousFileOperation):
        storage._clean_name("../secrets.txt")


@pytest.mark.unit
@override_settings(
    FTP_MEDIA_HOST="ftp.example",
    FTP_MEDIA_PORT=21,
    FTP_MEDIA_USERNAME="user",
    FTP_MEDIA_PASSWORD="password",
    FTP_MEDIA_USE_TLS=True,
    FTP_MEDIA_PASSIVE_MODE=True,
    FTP_MEDIA_TIMEOUT_SECONDS=5,
    FTP_MEDIA_REMOTE_ROOT="api-media",
    FTP_MEDIA_PUBLIC_BASE_URL="https://cdn.example/api-media",
)
def test_ftp_storage_save_read_delete_and_missing_file(monkeypatch):
    class FakeFTP:
        files = {}

        def __init__(self, **kwargs):
            pass

        def connect(self, *args):
            pass

        def login(self, *args):
            pass

        def prot_p(self):
            pass

        def set_pasv(self, *args):
            pass

        def mkd(self, *args):
            pass

        def quit(self):
            pass

        def close(self):
            pass

        def storbinary(self, command, content, **kwargs):
            self.files[command.split(" ", 1)[1]] = content.read()

        def rename(self, source, destination):
            self.files[destination] = self.files.pop(source)

        def retrbinary(self, command, callback):
            callback(self.files[command.split(" ", 1)[1]])

        def size(self, path):
            if path not in self.files:
                raise ftplib.error_perm("550 missing")
            return len(self.files[path])

        def delete(self, path):
            if path not in self.files:
                raise ftplib.error_perm("550 missing")
            del self.files[path]

    monkeypatch.setattr("apps.common.ftp_storage.ftplib.FTP_TLS", FakeFTP)
    storage = FTPMediaStorage()
    saved_name = storage._save("products/1/chair.jpg", BytesIO(b"photo"))
    assert saved_name == "products/1/chair.jpg"
    assert storage.exists(saved_name) is True
    assert storage.size(saved_name) == 5
    assert storage._open(saved_name).read() == b"photo"
    storage.delete(saved_name)
    assert storage.exists(saved_name) is False
    storage.delete(saved_name)


@pytest.mark.unit
@override_settings(
    FTP_MEDIA_HOST="ftp.example",
    FTP_MEDIA_PORT=21,
    FTP_MEDIA_USERNAME="user",
    FTP_MEDIA_PASSWORD="password",
    FTP_MEDIA_USE_TLS=True,
    FTP_MEDIA_PASSIVE_MODE=True,
    FTP_MEDIA_TIMEOUT_SECONDS=5,
    FTP_MEDIA_REMOTE_ROOT="api-media",
    FTP_MEDIA_PUBLIC_BASE_URL="https://cdn.example/api-media",
)
def test_ftp_storage_reuses_one_connection_for_batched_saves(monkeypatch):
    class FakeFTP:
        files = {}
        connects = 0

        def __init__(self, **kwargs):
            pass

        def connect(self, *args):
            type(self).connects += 1

        def login(self, *args):
            pass

        def prot_p(self):
            pass

        def set_pasv(self, *args):
            pass

        def mkd(self, *args):
            pass

        def quit(self):
            pass

        def close(self):
            pass

        def storbinary(self, command, content, **kwargs):
            self.files[command.split(" ", 1)[1]] = content.read()

        def rename(self, source, destination):
            self.files[destination] = self.files.pop(source)

        def size(self, path):
            if path not in self.files:
                raise ftplib.error_perm("550 missing")
            return len(self.files[path])

        def delete(self, path):
            if path not in self.files:
                raise ftplib.error_perm("550 missing")
            del self.files[path]

    FakeFTP.files = {}
    FakeFTP.connects = 0
    monkeypatch.setattr("apps.common.ftp_storage.ftplib.FTP_TLS", FakeFTP)
    storage = FTPMediaStorage()
    with storage.reuse_connection():
        storage._save("products/1/a.jpg", BytesIO(b"one"))
        storage._save("products/1/b.jpg", BytesIO(b"two"))
        assert storage.exists("products/1/a.jpg") is True
        assert storage.exists("products/1/b.jpg") is True
    assert FakeFTP.connects == 1
