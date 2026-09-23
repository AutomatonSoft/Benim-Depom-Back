from django.urls import path

from .views import (
    EmailVerificationResendView,
    EmailVerificationView,
    LoginView,
    LogoutView,
    MeView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetRequestView,
    PasswordResetVerifyView,
    PreferredLanguageView,
    RefreshView,
    RegisterView,
)

app_name = "accounts"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path(
        "email/verify/",
        EmailVerificationView.as_view(),
        name="email-verify",
    ),
    path(
        "email/resend-verification/",
        EmailVerificationResendView.as_view(),
        name="email-resend-verification",
    ),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        PasswordChangeView.as_view(),
        name="password-change",
    ),
    path(
        "password/reset/request/",
        PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "password/reset/verify/",
        PasswordResetVerifyView.as_view(),
        name="password-reset-verify",
    ),
    path(
        "password/reset/complete/",
        PasswordResetCompleteView.as_view(),
        name="password-reset-complete",
    ),
    path("me/language/", PreferredLanguageView.as_view(), name="me-language"),
    path("me/", MeView.as_view(), name="me"),
]
