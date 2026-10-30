from django.urls import path

from . import v1
from .urls import urlpatterns as shared_patterns

app_name = "accounts"

urlpatterns = [
    path("register/", v1.RegisterViewV1.as_view(), name="register"),
    path("login/", v1.LoginViewV1.as_view(), name="login"),
    path("me/", v1.MeViewV1.as_view(), name="me"),
    path("email/verify/", v1.EmailVerificationViewV1.as_view(), name="email-verify"),
    path(
        "email/resend-verification/",
        v1.EmailVerificationResendViewV1.as_view(),
        name="email-resend-verification",
    ),
    path(
        "password/reset/request/",
        v1.PasswordResetRequestViewV1.as_view(),
        name="password-reset-request",
    ),
    path(
        "password/reset/verify/",
        v1.PasswordResetVerifyViewV1.as_view(),
        name="password-reset-verify",
    ),
]
overridden_names = {pattern.name for pattern in urlpatterns}
urlpatterns += [
    pattern for pattern in shared_patterns if pattern.name not in overridden_names
]
