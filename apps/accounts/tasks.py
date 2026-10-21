from smtplib import SMTPException

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from apps.common.tasks import enqueue_email_task

from .emails import build_email_verification_message


@shared_task(
    autoretry_for=(OSError, SMTPException),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    ignore_result=True,
)
def send_seller_registered_email(*, seller_name: str, seller_email: str) -> None:
    recipient = settings.PRODUCT_CREATE_EMAIL
    if not recipient:
        return

    manager_url = f"{settings.PUBLIC_BASE_URL}/manager/sellers"
    message = EmailMultiAlternatives(
        subject="Benim Depom — a new seller has registered",
        body=(
            f"A new seller has registered: {seller_name} ({seller_email}).\n\n"
            "The seller has verified their email and completed registration.\n\n"
            "Please open the manager panel to view the seller:\n"
            f"{manager_url}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(
        render_to_string(
            "emails/seller_registered.html",
            {
                "seller_name": seller_name,
                "seller_email": seller_email,
                "manager_url": manager_url,
            },
        ),
        "text/html",
    )
    message.send(fail_silently=False)


def enqueue_seller_registered_email(*, seller_name: str, seller_email: str) -> None:
    if settings.PRODUCT_CREATE_EMAIL:
        enqueue_email_task(
            send_seller_registered_email,
            seller_name=seller_name,
            seller_email=seller_email,
        )


@shared_task(
    autoretry_for=(OSError, SMTPException),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def send_email_verification_code(*, email: str, code: str) -> None:
    subject, text_body, html_body = build_email_verification_message(code=code)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


@shared_task(
    autoretry_for=(OSError, SMTPException),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def send_password_reset_code(*, email: str, code: str) -> None:
    subject = "Reset your Benim Depom password"
    text_body = (
        f"Your password reset code: {code}\n\n"
        f"The code expires in {settings.PASSWORD_RESET_CODE_TTL_MINUTES} minutes."
    )
    html_body = (
        "<p>Your password reset code:</p>"
        f"<h2>{code}</h2>"
        "<p>The code expires in "
        f"{settings.PASSWORD_RESET_CODE_TTL_MINUTES} minutes.</p>"
    )

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


@shared_task(bind=True, max_retries=40, default_retry_delay=20)
def finalize_seller_purge(self, seller_id: int) -> str:
    from apps.accounts.purge import try_finalize_seller_purge

    result = try_finalize_seller_purge(seller_id=seller_id)
    if result == "busy":
        raise self.retry()
    return result
