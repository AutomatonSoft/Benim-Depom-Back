from smtplib import SMTPException
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.core import mail

from apps.accounts.tasks import (
    enqueue_seller_registered_email,
    send_seller_registered_email,
)

pytestmark = pytest.mark.unit
PAYLOAD = {"seller_name": 'Seller <b> & "Co"', "seller_email": "seller@example.com"}


@pytest.mark.parametrize(
    "base_url", ["https://benimdepom.info", "https://stage.benimdepom.info"]
)
def test_seller_email_is_english_and_escapes_seller_details(settings, base_url):
    settings.PUBLIC_BASE_URL = base_url
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    send_seller_registered_email.run(**PAYLOAD)
    message = mail.outbox[-1]
    html, content_type = message.alternatives[0]
    assert message.to == ["info@jvmoebel.de"]
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert "a new seller has registered" in message.subject
    assert "verified their email and completed registration" in message.body
    assert all(value in message.body for value in PAYLOAD.values())
    assert content_type == "text/html"
    assert '<html lang="en">' in html
    assert "Seller &lt;b&gt; &amp; &quot;Co&quot;" in html
    assert "seller@example.com" in html
    assert "<b>" not in html
    assert f'href="{base_url}/manager/sellers"' in html
    assert f"{base_url}/manager/sellers" in message.body
    assert "View sellers" in html
    assert html.count("href=") == 1


def test_seller_email_disabled_without_recipient(settings):
    settings.PRODUCT_CREATE_EMAIL = ""
    with patch("apps.accounts.tasks.enqueue_email_task") as enqueue:
        enqueue_seller_registered_email(**PAYLOAD)
    enqueue.assert_not_called()
    with patch("apps.accounts.tasks.EmailMultiAlternatives") as message:
        send_seller_registered_email.run(**PAYLOAD)
    message.assert_not_called()


def test_seller_email_is_queued_with_bounded_connection(settings):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    with (
        patch.object(
            send_seller_registered_email.app, "connection_for_write"
        ) as connect,
        patch.object(send_seller_registered_email, "apply_async") as publish,
        patch("apps.accounts.tasks.EmailMultiAlternatives") as message,
    ):
        enqueue_seller_registered_email(**PAYLOAD)
    publish.assert_called_once_with(
        kwargs=PAYLOAD,
        connection=connect.return_value.__enter__.return_value,
        retry=False,
    )
    assert connect.call_args.kwargs["transport_options"]["max_retries"] == 0
    assert connect.call_args.kwargs["transport_options"]["socket_timeout"] == 1
    message.assert_not_called()


def test_seller_email_retries_smtp_failure(settings):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    with (
        patch(
            "apps.accounts.tasks.EmailMultiAlternatives.send", side_effect=SMTPException
        ),
        patch.object(send_seller_registered_email, "retry", side_effect=Retry) as retry,
        pytest.raises(Retry),
    ):
        send_seller_registered_email.run(**PAYLOAD)
    retry.assert_called_once()
