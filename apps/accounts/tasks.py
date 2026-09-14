from smtplib import SMTPException

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .emails import build_email_verification_message


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
