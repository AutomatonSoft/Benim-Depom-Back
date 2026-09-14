from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import sleep
from xml.sax.saxutils import escape

import requests
from django.conf import settings

AFTERBUY_ENDPOINT = "https://api.afterbuy.de/afterbuy/ABInterface.aspx"


class AfterbuyClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AfterbuyAccountCredentials:
    account: str
    partner_token: str
    account_token: str
    partner_id: str = ""

    def is_configured(self) -> bool:
        return bool(self.partner_token and self.account_token)


def afterbuy_credentials(account: str) -> AfterbuyAccountCredentials:
    key = account.upper()
    return AfterbuyAccountCredentials(
        account=account,
        partner_token=getattr(settings, f"AFTERBUY_{key}_PARTNER_TOKEN", "") or "",
        account_token=getattr(settings, f"AFTERBUY_{key}_ACCOUNT_TOKEN", "") or "",
        partner_id=getattr(settings, f"AFTERBUY_{key}_PARTNER_ID", "") or "",
    )


def format_afterbuy_datetime(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S")


def build_get_sold_items_xml(
    *,
    credentials: AfterbuyAccountCredentials,
    date_from: datetime,
    date_to: datetime,
    range_id_from: str | None = None,
    max_sold_items: int = 100,
) -> str:
    partner_id_xml = ""
    if credentials.partner_id:
        partner_id_xml = f"<PartnerID>{escape(credentials.partner_id)}</PartnerID>"

    range_xml = ""
    if range_id_from:
        range_xml = (
            "<Filter>"
            "<FilterName>RangeID</FilterName>"
            "<FilterValues>"
            f"<ValueFrom>{escape(str(range_id_from))}</ValueFrom>"
            "</FilterValues>"
            "</Filter>"
        )

    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Request>"
        "<AfterbuyGlobal>"
        f"{partner_id_xml}"
        f"<PartnerToken>{escape(credentials.partner_token)}</PartnerToken>"
        f"<AccountToken>{escape(credentials.account_token)}</AccountToken>"
        "<CallName>GetSoldItems</CallName>"
        "<DetailLevel>0</DetailLevel>"
        "<ErrorLanguage>EN</ErrorLanguage>"
        "</AfterbuyGlobal>"
        f"<MaxSoldItems>{int(max_sold_items)}</MaxSoldItems>"
        "<OrderDirection>0</OrderDirection>"
        "<DataFilter>"
        "<Filter>"
        "<FilterName>DateFilter</FilterName>"
        "<FilterValues>"
        f"<DateFrom>{escape(format_afterbuy_datetime(date_from))}</DateFrom>"
        f"<DateTo>{escape(format_afterbuy_datetime(date_to))}</DateTo>"
        "<FilterValue>PayDate</FilterValue>"
        "</FilterValues>"
        "</Filter>"
        f"{range_xml}"
        "</DataFilter>"
        "</Request>"
    )


def fetch_sold_items_xml(
    *,
    credentials: AfterbuyAccountCredentials,
    date_from: datetime,
    date_to: datetime,
    range_id_from: str | None = None,
) -> str:
    body = build_get_sold_items_xml(
        credentials=credentials,
        date_from=date_from,
        date_to=date_to,
        range_id_from=range_id_from,
        max_sold_items=settings.AFTERBUY_MAX_SOLD_ITEMS,
    )
    timeout = settings.AFTERBUY_HTTP_TIMEOUT_SECONDS
    retries = settings.AFTERBUY_HTTP_RETRIES
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.post(
                AFTERBUY_ENDPOINT,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/xml; charset=utf-8"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.text
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            if attempt == retries - 1:
                break
            sleep(2**attempt)
            continue
        except requests.HTTPError as exc:
            raise AfterbuyClientError("Afterbuy HTTP error.") from exc
    raise AfterbuyClientError("Afterbuy request failed.") from last_error
