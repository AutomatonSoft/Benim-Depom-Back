import csv
import hashlib
import gzip
import io
import json
import os
import re
import tempfile
import time
import zipfile
from contextlib import contextmanager

import requests

from apps.common.marketplace_http import build_marketplace_session
from apps.marketplace.ebay.client import api_base_url

from .errors import BulkEndError, EBayRateLimitError, EBayTransportError, ReportStorageError

FEED_TYPE = "LMS_ACTIVE_INVENTORY_REPORT"
MARKETPLACE_ID = "EBAY_DE"
MAX_SKU_LENGTH = 50
REPORT_PATH = "/sell/feed/v1"


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
    }


def _check_response(response):
    if response.status_code == 429:
        try:
            retry_after = min(60, max(1, int(response.headers.get("Retry-After", "30"))))
        except (TypeError, ValueError):
            retry_after = 30
        raise EBayRateLimitError(retry_after)
    if response.status_code in {401, 403}:
        raise BulkEndError(
            "Для отчёта eBay требуется разрешение sell.inventory. Переподключите выбранный аккаунт, если доступ не был выдан."
        )
    if response.status_code >= 500:
        raise EBayTransportError("eBay временно не обработал запрос к отчёту.")
    if not response.ok:
        raise BulkEndError(f"eBay отклонил запрос к отчёту (HTTP {response.status_code}).")


def _json_response(response):
    _check_response(response)
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise EBayTransportError("eBay вернул некорректный ответ отчёта.") from error
    if not isinstance(payload, dict):
        raise EBayTransportError("eBay вернул некорректный ответ отчёта.")
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
        raise EBayTransportError("Не удалось связаться с eBay для создания отчёта.") from error
    _check_response(response)
    location = response.headers.get("Location", "")
    match = re.search(r"/inventory_task/([A-Za-z0-9._-]{1,255})/?$", location)
    if not match:
        raise EBayTransportError("eBay не вернул идентификатор задания отчёта.")
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
        raise EBayTransportError("Не удалось проверить статус отчёта eBay.") from error
    payload = _json_response(response)
    if payload.get("taskId") != task_id or payload.get("feedType") != FEED_TYPE:
        raise EBayTransportError("eBay вернул статус другого задания отчёта.")
    return payload


def get_inventory_tasks(token, date_range):
    try:
        with build_marketplace_session() as session:
            response = session.get(
                f"{api_base_url()}{REPORT_PATH}/inventory_task",
                headers=_headers(token),
                params={"feed_type": FEED_TYPE, "date_range": date_range, "limit": 500, "offset": 0},
                timeout=(5, 30),
            )
    except requests.RequestException as error:
        raise EBayTransportError("Не удалось восстановить задание отчёта eBay.") from error
    payload = _json_response(response)
    tasks = payload.get("tasks")
    total = payload.get("total")
    if (
        not isinstance(tasks, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(tasks)
        or any(not isinstance(task, dict) for task in tasks)
    ):
        raise EBayTransportError("eBay вернул некорректный список заданий отчёта.")
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
                    headers={**_headers(token), "Accept": "application/octet-stream, text/csv"},
                    timeout=(5, 60),
                    stream=True,
                ) as response:
                    _check_response(response)
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        fileobj.write(chunk)
        except requests.RequestException as error:
            raise EBayTransportError("Не удалось скачать отчёт eBay.") from error
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
                        headers={**_headers(token), "Accept": "application/octet-stream, text/csv"},
                        timeout=(5, 60),
                        stream=True,
                    ) as response:
                        _check_response(response)
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                output.write(chunk)
                                pulse()
            except requests.RequestException as error:
                raise EBayTransportError("Не удалось скачать отчёт eBay.") from error
            output.flush()
            os.fsync(output.fileno())

        if os.path.getsize(raw_path) == 0:
            raise EBayTransportError("eBay вернул пустой файл отчёта.")

        digest = hashlib.sha256()
        normalized_size = 0
        with open(raw_path, "rb") as raw:
            try:
                archive, raw_stream = _csv_stream(raw)
            except (zipfile.BadZipFile, EOFError, OSError) as error:
                raise BulkEndError("Повреждённый ZIP-отчёт eBay.") from error
            try:
                with open(normalized_path, "xb") as normalized:
                    while True:
                        chunk = raw_stream.read(64 * 1024)
                        if not chunk:
                            break
                        normalized.write(chunk)
                        digest.update(chunk)
                        normalized_size += len(chunk)
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

        if normalized_size == 0:
            raise BulkEndError("eBay вернул пустой CSV-отчёт.")
        os.chmod(normalized_path, 0o600)
        os.replace(normalized_path, target_path)
        return {
            "path": target_path,
            "size": normalized_size,
            "sha256": digest.hexdigest(),
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
        raise ReportStorageError("Не удалось проверить сохранённый отчёт eBay.") from error
    if not size:
        raise ReportStorageError("Сохранённый отчёт eBay пуст.")
    return {"path": os.fspath(path), "size": size, "sha256": digest.hexdigest()}


def _csv_stream(fileobj):
    fileobj.seek(0)
    signature = fileobj.read(4)
    if signature == b"PK\x03\x04":
        fileobj.seek(0)
        archive = zipfile.ZipFile(fileobj)
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            archive.close()
            raise BulkEndError("В ZIP-отчёте eBay отсутствует единственный CSV-файл.")
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
        reader = csv.DictReader(text_stream)
        if not reader.fieldnames:
            raise BulkEndError("В отчёте eBay отсутствует заголовок CSV.")
        columns = {name.strip().casefold(): name for name in reader.fieldnames if name}
        required = {"sku", "itemid", "channel"}
        if not required.issubset(columns):
            raise BulkEndError("В отчёте eBay отсутствуют обязательные столбцы SKU, ItemID или Channel.")
        for row in reader:
            if None in row:
                raise BulkEndError("Отчёт eBay содержит строку с некорректным числом столбцов.")
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
        reader = csv.DictReader(_ReadlineIterator(text_stream))
        if not reader.fieldnames:
            raise BulkEndError("В отчёте eBay отсутствует заголовок CSV.")
        columns = {name.strip().casefold(): name for name in reader.fieldnames if name}
        if not {"sku", "itemid", "channel"}.issubset(columns):
            raise BulkEndError("В отчёте eBay отсутствуют обязательные столбцы SKU, ItemID или Channel.")
        fieldnames = reader.fieldnames
        if start_cookie:
            text_stream.seek(int(start_cookie))
            reader = csv.DictReader(_ReadlineIterator(text_stream), fieldnames=fieldnames)
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
