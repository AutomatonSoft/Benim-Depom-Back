import csv
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import time
import zipfile
from contextlib import contextmanager
from urllib.parse import urlsplit

import requests

from apps.common.marketplace_http import build_marketplace_session
from apps.marketplace.ebay.client import api_base_url

from .errors import (
    BulkEndError,
    EBayRateLimitError,
    EBayTransportError,
    ReportStorageError,
)
from .xml_report import iter_xml_inventory_rows

FEED_TYPE = "LMS_ACTIVE_INVENTORY_REPORT"
MARKETPLACE_ID = "EBAY_DE"
MAX_SKU_LENGTH = 255
REPORT_PATH = "/sell/feed/v1"


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
    }


def _response_diagnostics(response, operation, reason):
    # Only allowlisted codes/identifiers; response bodies and request headers can contain secrets.
    details = {
        "operation": operation,
        "reason": reason,
        "http_status": response.status_code,
    }
    for header in ("X-EBAY-C-REQUEST-ID", "Rlogid"):
        value = response.headers.get(header)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:%=+-]{1,255}", value):
            details[header.lower()] = value
    if reason != "http_error":
        return details
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return details
    if isinstance(payload, dict) and isinstance(payload.get("errors"), list):
        details["ebay_error_ids"] = [
            error["errorId"]
            for error in payload["errors"][:5]
            if isinstance(error, dict) and type(error.get("errorId")) is int
        ]
    return details


def _check_response(response, operation="feed"):
    if response.ok:
        return
    diagnostics = _response_diagnostics(response, operation, "http_error")
    if response.status_code == 429:
        try:
            retry_after = min(
                60, max(1, int(response.headers.get("Retry-After", "30")))
            )
        except (TypeError, ValueError):
            retry_after = 30
        raise EBayRateLimitError(retry_after, diagnostics=diagnostics)
    if response.status_code in {401, 403}:
        raise BulkEndError(
            "Для отчёта eBay требуется разрешение sell.inventory. Переподключите выбранный аккаунт, если доступ не был выдан.",
            diagnostics=diagnostics,
        )
    if response.status_code >= 500:
        raise EBayTransportError(
            "eBay временно не обработал запрос к отчёту.", diagnostics=diagnostics
        )
    if not response.ok:
        raise BulkEndError(
            f"eBay отклонил запрос к отчёту (HTTP {response.status_code}).",
            diagnostics=diagnostics,
        )


def _json_response(response, operation="feed"):
    _check_response(response, operation)
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise EBayTransportError(
            "eBay вернул некорректный ответ отчёта.",
            diagnostics=_response_diagnostics(response, operation, "invalid_json"),
        ) from error
    if not isinstance(payload, dict):
        raise EBayTransportError(
            "eBay вернул некорректный ответ отчёта.",
            diagnostics=_response_diagnostics(response, operation, "invalid_json"),
        )
    return payload


def create_inventory_task(token):
    try:
        with build_marketplace_session() as session:
            response = session.post(
                f"{api_base_url()}{REPORT_PATH}/inventory_task",
                headers={**_headers(token), "Content-Type": "application/json"},
                json={"schemaVersion": "1.0", "feedType": FEED_TYPE},
                timeout=(5, 30),
            )
    except requests.RequestException as error:
        raise EBayTransportError(
            "Не удалось связаться с eBay для создания отчёта.",
            diagnostics={
                "operation": "create_inventory_task",
                "reason": "network_error",
                "exception_type": type(error).__name__,
            },
        ) from error
    _check_response(response, "create_inventory_task")
    location = response.headers.get("Location", "")
    # eBay also returns /task/{id} for inventory report creation.
    match = re.fullmatch(
        r"/sell/feed/v1/(?:inventory_task|task)/([A-Za-z0-9._-]{1,255})/?",
        urlsplit(location).path,
    )
    if not match:
        raise EBayTransportError(
            "eBay не вернул идентификатор задания отчёта.",
            diagnostics=_response_diagnostics(
                response, "create_inventory_task", "missing_task_id"
            ),
        )
    return match.group(1)


def get_inventory_task(token, task_id):
    try:
        with build_marketplace_session() as session:
            response = session.get(
                f"{api_base_url()}{REPORT_PATH}/inventory_task/{task_id}",
                headers=_headers(token),
                timeout=(5, 30),
            )
    except requests.RequestException as error:
        raise EBayTransportError(
            "Не удалось проверить статус отчёта eBay.",
            diagnostics={
                "operation": "get_inventory_task",
                "reason": "network_error",
                "exception_type": type(error).__name__,
            },
        ) from error
    payload = _json_response(response, "get_inventory_task")
    if payload.get("taskId") != task_id or payload.get("feedType") != FEED_TYPE:
        raise EBayTransportError(
            "eBay вернул статус другого задания отчёта.",
            diagnostics=_response_diagnostics(
                response, "get_inventory_task", "wrong_task_identity"
            ),
        )
    return payload


def get_inventory_tasks(token, date_range):
    try:
        with build_marketplace_session() as session:
            response = session.get(
                f"{api_base_url()}{REPORT_PATH}/inventory_task",
                headers=_headers(token),
                params={
                    "feed_type": FEED_TYPE,
                    "date_range": date_range,
                    "limit": 500,
                    "offset": 0,
                },
                timeout=(5, 30),
            )
    except requests.RequestException as error:
        raise EBayTransportError(
            "Не удалось восстановить задание отчёта eBay.",
            diagnostics={
                "operation": "get_inventory_tasks",
                "reason": "network_error",
                "exception_type": type(error).__name__,
            },
        ) from error
    payload = _json_response(response, "get_inventory_tasks")
    tasks = payload.get("tasks")
    total = payload.get("total")
    if (
        not isinstance(tasks, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(tasks)
        or any(not isinstance(task, dict) for task in tasks)
    ):
        raise EBayTransportError(
            "eBay вернул некорректный список заданий отчёта.",
            diagnostics=_response_diagnostics(
                response, "get_inventory_tasks", "invalid_task_list"
            ),
        )
    return tasks, total


@contextmanager
def download_inventory_report(token, task_id):
    # Keep only 2 MiB in RAM; larger reports spill to disk instead of imposing an account-size cap.
    fileobj = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    try:
        try:
            with build_marketplace_session() as session:
                with session.get(
                    f"{api_base_url()}{REPORT_PATH}/task/{task_id}/download_result_file",
                    headers={
                        **_headers(token),
                        "Accept": "application/octet-stream, text/csv",
                    },
                    timeout=(5, 60),
                    stream=True,
                ) as response:
                    _check_response(response, "download_inventory_report")
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        fileobj.write(chunk)
        except requests.RequestException as error:
            raise EBayTransportError(
                "Не удалось скачать отчёт eBay.",
                diagnostics={
                    "operation": "download_inventory_report",
                    "reason": "network_error",
                    "exception_type": type(error).__name__,
                },
            ) from error
        except OSError as error:
            raise EBayTransportError(
                "Не удалось сохранить полный отчёт eBay во временное хранилище; проверьте свободное место."
            ) from error
        if fileobj.tell() == 0:
            raise EBayTransportError("eBay вернул пустой файл отчёта.")
        fileobj.seek(0)
        yield fileobj
    finally:
        fileobj.close()


def download_inventory_report_to_path(token, task_id, target_path, *, heartbeat=None):
    """Stage a complete, normalized CSV on the shared persistent application volume."""
    target_path = os.fspath(target_path)
    directory = os.path.dirname(target_path)
    raw_path = target_path + ".download.part"
    normalized_path = target_path + ".normalize.part"
    last_heartbeat = time.monotonic()

    def pulse():
        nonlocal last_heartbeat
        if heartbeat and time.monotonic() - last_heartbeat >= 20:
            heartbeat()
            last_heartbeat = time.monotonic()

    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(raw_path, "xb") as output:
            try:
                with build_marketplace_session() as session:
                    with session.get(
                        f"{api_base_url()}{REPORT_PATH}/task/{task_id}/download_result_file",
                        headers={
                            **_headers(token),
                            "Accept": "application/octet-stream, text/csv",
                        },
                        timeout=(5, 60),
                        stream=True,
                    ) as response:
                        _check_response(response, "download_inventory_report")
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                output.write(chunk)
                                pulse()
            except requests.RequestException as error:
                raise EBayTransportError(
                    "Не удалось скачать отчёт eBay.",
                    diagnostics={
                        "operation": "download_inventory_report",
                        "reason": "network_error",
                        "exception_type": type(error).__name__,
                    },
                ) from error
            output.flush()
            os.fsync(output.fileno())

        if os.path.getsize(raw_path) == 0:
            raise EBayTransportError("eBay вернул пустой файл отчёта.")

        with open(raw_path, "rb") as raw:
            try:
                archive, raw_stream = _csv_stream(raw)
            except (zipfile.BadZipFile, EOFError, OSError) as error:
                raise BulkEndError("Повреждённый ZIP-отчёт eBay.") from error
            try:
                prefix = raw_stream.read(256)
                raw_stream.seek(0)
                with open(normalized_path, "xb") as normalized:
                    if prefix.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"<"):
                        wrapper = io.TextIOWrapper(
                            normalized, encoding="utf-8", newline=""
                        )
                        try:
                            writer = csv.writer(wrapper)
                            writer.writerow(("SKU", "ItemID", "Channel"))
                            for row in iter_xml_inventory_rows(
                                raw_stream, MARKETPLACE_ID, pulse
                            ):
                                row = _inventory_row(
                                    row,
                                    {
                                        "sku": "sku",
                                        "itemid": "item_id",
                                        "channel": "marketplace_id",
                                    },
                                )
                                writer.writerow(
                                    (row["sku"], row["item_id"], row["marketplace_id"])
                                )
                            wrapper.flush()
                        finally:
                            wrapper.detach()
                    else:
                        while chunk := raw_stream.read(64 * 1024):
                            normalized.write(chunk)
                            pulse()
                    normalized.flush()
                    os.fsync(normalized.fileno())
            except (gzip.BadGzipFile, EOFError, OSError, zipfile.BadZipFile) as error:
                raise BulkEndError("Повреждённый сжатый CSV-отчёт eBay.") from error
            finally:
                if archive:
                    archive.close()
                elif raw_stream is not raw:
                    raw_stream.close()

        staged = inspect_staged_inventory_report(normalized_path, heartbeat=heartbeat)
        os.chmod(normalized_path, 0o600)
        os.replace(normalized_path, target_path)
        return {
            "path": target_path,
            "size": staged["size"],
            "sha256": staged["sha256"],
        }
    except OSError as error:
        raise ReportStorageError(
            "Не удалось сохранить полный отчёт eBay в общее временное хранилище; проверьте свободное место."
        ) from error
    finally:
        for path in (raw_path, normalized_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def inspect_staged_inventory_report(path, *, heartbeat=None):
    digest = hashlib.sha256()
    size = 0
    last_heartbeat = time.monotonic()
    try:
        with open(path, "rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
                size += len(chunk)
                if heartbeat and time.monotonic() - last_heartbeat >= 20:
                    heartbeat()
                    last_heartbeat = time.monotonic()
    except OSError as error:
        raise ReportStorageError(
            "Не удалось проверить сохранённый отчёт eBay."
        ) from error
    if not size:
        raise ReportStorageError("Сохранённый отчёт eBay пуст.")
    return {"path": os.fspath(path), "size": size, "sha256": digest.hexdigest()}


def _csv_stream(fileobj):
    fileobj.seek(0)
    signature = fileobj.read(4)
    if signature == b"PK\x03\x04":
        fileobj.seek(0)
        archive = zipfile.ZipFile(fileobj)
        csv_names = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".csv", ".xml"))
        ]
        if len(csv_names) != 1:
            archive.close()
            raise BulkEndError(
                "В ZIP-отчёте eBay отсутствует единственный CSV/XML-файл."
            )
        stream = archive.open(csv_names[0])
        return archive, stream
    if signature[:2] == b"\x1f\x8b":
        fileobj.seek(0)
        stream = gzip.GzipFile(fileobj=fileobj, mode="rb")
        return stream, stream
    fileobj.seek(0)
    return None, fileobj


def iter_inventory_rows(fileobj):
    archive, raw_stream = _csv_stream(fileobj)
    text_stream = io.TextIOWrapper(raw_stream, encoding="utf-8-sig", newline="")
    try:
        reader = csv.DictReader(text_stream, strict=True)
        if not reader.fieldnames:
            raise BulkEndError("В отчёте eBay отсутствует заголовок CSV.")
        columns = {name.strip().casefold(): name for name in reader.fieldnames if name}
        required = {"sku", "itemid", "channel"}
        if not required.issubset(columns):
            raise BulkEndError(
                "В отчёте eBay отсутствуют обязательные столбцы SKU, ItemID или Channel."
            )
        for row in reader:
            if None in row:
                raise BulkEndError(
                    "Отчёт eBay содержит строку с некорректным числом столбцов."
                )
            sku = (row.get(columns["sku"]) or "").strip()
            item_id = (row.get(columns["itemid"]) or "").strip()
            marketplace_id = (row.get(columns["channel"]) or "").strip()
            if not item_id.isascii() or not item_id.isdigit() or len(item_id) > 20:
                raise BulkEndError("В отчёте eBay обнаружен некорректный ItemID.")
            if not marketplace_id:
                raise BulkEndError("В отчёте eBay не указан маркетплейс объявления.")
            if len(sku) > MAX_SKU_LENGTH:
                raise BulkEndError("В отчёте eBay обнаружен слишком длинный SKU.")
            yield {"sku": sku, "item_id": item_id, "marketplace_id": marketplace_id}
    except UnicodeDecodeError as error:
        raise BulkEndError("Не удалось прочитать кодировку CSV-отчёта eBay.") from error
    except csv.Error as error:
        raise BulkEndError("Отчёт eBay содержит повреждённую строку CSV.") from error
    finally:
        try:
            text_stream.detach()
        except (ValueError, AttributeError):
            pass
        if archive:
            archive.close()


def iter_inventory_chunk(fileobj, start_cookie, max_rows):
    text_stream = io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline="")
    try:
        reader = csv.DictReader(_ReadlineIterator(text_stream), strict=True)
        if not reader.fieldnames:
            raise BulkEndError("В отчёте eBay отсутствует заголовок CSV.")
        columns = {name.strip().casefold(): name for name in reader.fieldnames if name}
        if not {"sku", "itemid", "channel"}.issubset(columns):
            raise BulkEndError(
                "В отчёте eBay отсутствуют обязательные столбцы SKU, ItemID или Channel."
            )
        fieldnames = reader.fieldnames
        if start_cookie:
            text_stream.seek(int(start_cookie))
            reader = csv.DictReader(
                _ReadlineIterator(text_stream), fieldnames=fieldnames, strict=True
            )
        else:
            start_cookie = str(text_stream.tell())

        rows = []
        next_cookie = str(start_cookie)
        eof = False
        for _ in range(max_rows):
            try:
                row = next(reader)
            except StopIteration:
                eof = True
                break
            rows.append(_inventory_row(row, columns))
            next_cookie = str(text_stream.tell())
        else:
            try:
                next(reader)
            except StopIteration:
                eof = True
        return rows, next_cookie, eof
    except UnicodeDecodeError as error:
        raise BulkEndError("Не удалось прочитать кодировку CSV-отчёта eBay.") from error
    except csv.Error as error:
        raise BulkEndError("Отчёт eBay содержит повреждённую строку CSV.") from error
    finally:
        try:
            text_stream.detach()
        except (ValueError, AttributeError):
            pass


class _ReadlineIterator:
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        return self

    def __next__(self):
        line = self.stream.readline()
        if not line:
            raise StopIteration
        return line


def _inventory_row(row, columns):
    if None in row:
        raise BulkEndError("Отчёт eBay содержит строку с некорректным числом столбцов.")
    sku = (row.get(columns["sku"]) or "").strip()
    item_id = (row.get(columns["itemid"]) or "").strip()
    marketplace_id = (row.get(columns["channel"]) or "").strip()
    if not item_id.isascii() or not item_id.isdigit() or len(item_id) > 20:
        raise BulkEndError("В отчёте eBay обнаружен некорректный ItemID.")
    if not marketplace_id:
        raise BulkEndError("В отчёте eBay не указан marketplace объявления.")
    if len(sku) > MAX_SKU_LENGTH:
        raise BulkEndError("В отчёте eBay обнаружен слишком длинный SKU.")
    return {"sku": sku, "item_id": item_id, "marketplace_id": marketplace_id}
