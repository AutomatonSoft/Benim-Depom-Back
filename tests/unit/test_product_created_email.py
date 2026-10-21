from smtplib import SMTPException
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.core import mail

from apps.products.tasks import (
    enqueue_product_created_email,
    send_product_created_email,
)

pytestmark = pytest.mark.unit

PAYLOAD = {
    "seller_name": 'Seller <b> & "Co"',
    "seller_email": "seller@example.com",
    "title": "Chair <script>alert(1)</script>",
}


def test_product_email_content_and_html_escaping(settings):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    send_product_created_email.run(**PAYLOAD)

    message = mail.outbox[-1]
    assert message.to == ["info@jvmoebel.de"]
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert all(value in message.body for value in PAYLOAD.values())
    html, content_type = message.alternatives[0]
    assert content_type == "text/html"
    assert "Seller &lt;b&gt; &amp; &quot;Co&quot;" in html
    assert "Chair &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "seller@example.com" in html
    assert "<script>" not in html
    assert 'href="https://benimdepom.info/manager/"' in html
    assert "a seller created a new product" in message.subject
    assert "Please open the manager panel" in message.body
    assert "Open manager panel" in html
    assert '<html lang="en">' in html
    assert html.count("href=") == 1


def test_product_email_disabled_without_recipient(settings):
    settings.PRODUCT_CREATE_EMAIL = ""
    with patch.object(send_product_created_email, "apply_async") as publish:
        enqueue_product_created_email(**PAYLOAD)
    publish.assert_not_called()
    with patch("apps.products.tasks.EmailMultiAlternatives") as message:
        send_product_created_email.run(**PAYLOAD)
    message.assert_not_called()


def test_enqueue_uses_bounded_connection_and_never_sends_mail_inline(settings):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    with (
        patch.object(send_product_created_email.app, "connection_for_write") as connect,
        patch.object(send_product_created_email, "apply_async") as publish,
        patch("apps.products.tasks.EmailMultiAlternatives") as message,
    ):
        enqueue_product_created_email(**PAYLOAD)
    publish.assert_called_once_with(
        kwargs=PAYLOAD,
        connection=connect.return_value.__enter__.return_value,
        retry=False,
    )
    options = connect.call_args.kwargs
    assert options["connect_timeout"] == 1
    assert options["transport_options"]["max_retries"] == 0
    assert options["transport_options"]["socket_connect_timeout"] == 1
    assert options["transport_options"]["socket_timeout"] == 1
    message.assert_not_called()


def test_enqueue_failure_is_logged_and_suppressed(settings, caplog):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    with patch.object(
        send_product_created_email.app,
        "connection_for_write",
        side_effect=OSError("Broker unavailable"),
    ):
        enqueue_product_created_email(**PAYLOAD)
    assert "Could not enqueue email task" in caplog.text


def test_smtp_failure_is_retried_in_worker(settings):
    settings.PRODUCT_CREATE_EMAIL = "info@jvmoebel.de"
    with (
        patch(
            "apps.products.tasks.EmailMultiAlternatives.send", side_effect=SMTPException
        ),
        patch.object(send_product_created_email, "retry", side_effect=Retry) as retry,
        pytest.raises(Retry),
    ):
        send_product_created_email.run(**PAYLOAD)
    retry.assert_called_once()
