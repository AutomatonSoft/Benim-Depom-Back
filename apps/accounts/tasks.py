from smtplib import SMTPException

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives



@shared_task(
    autoretry_for=(OSError, SMTPException),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def send_email_verification_code(*, email: str, code: str) -> None:
    subject = "Подтверждение регистрации"
    text_body = (
        f"Ваш код подтверждения: {code}\n\n"
        f"Код действует "
        f"{settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES} минут."
    )
    html_body = (
        "<p>Ваш код подтверждения:</p>"
        f"<h2>{code}</h2>"
        f"<p>Код действует "
        f"{settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES} минут.</p>"
    )

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)