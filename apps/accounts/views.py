from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.common.permissions import IsManager, IsSeller
from apps.common.throttles import (
    EmailVerificationRateThrottle,
    EmailVerificationResendRateThrottle,
    LoginRateThrottle,
    ManagerMutationThrottleMixin,
    PasswordResetRequestRateThrottle,
    PasswordResetVerifyRateThrottle,
    RegistrationRateThrottle,
)

from .models import User
from .purge import purge_seller
from .serializers import (
    EmailTokenObtainPairSerializer,
    EmailVerificationResendSerializer,
    EmailVerificationSerializer,
    LogoutSerializer,
    ManagerCreateSerializer,
    ManagerSellerSerializer,
    PasswordChangeSerializer,
    PasswordResetCompleteSerializer,
    PasswordResetRequestResponseSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifyResponseSerializer,
    PasswordResetVerifySerializer,
    ProfileSerializer,
    RegisterSerializer,
)
from .services import (
    complete_password_reset,
    issue_email_verification_code,
    manager_confirm_seller_email,
    request_password_reset,
    resend_email_verification_code,
    revoke_refresh_tokens,
    verify_email_code,
    verify_password_reset_code,
)
from .tasks import send_email_verification_code, send_password_reset_code


class RegisterView(generics.CreateAPIView):
    throttle_classes = [RegistrationRateThrottle]
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            user = serializer.save()
            code = issue_email_verification_code(user=user)

            transaction.on_commit(
                lambda: send_email_verification_code.delay(
                    email=user.email,
                    code=code,
                )
            )

        return Response(
            {
                "user": ProfileSerializer(
                    user,
                    context={"request": request},
                ).data,
                "email_verification_required": True,
            },
            status=status.HTTP_201_CREATED,
        )


class EmailVerificationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [EmailVerificationRateThrottle]

    @extend_schema(
        request=EmailVerificationSerializer,
        responses={200: dict},
        description=("Verifies the six-digit email code and returns JWT tokens."),
    )
    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = verify_email_code(
            email=serializer.validated_data["email"],
            code=serializer.validated_data["code"],
        )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "user": ProfileSerializer(
                    user,
                    context={"request": request},
                ).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_200_OK,
        )


class EmailVerificationResendView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [EmailVerificationResendRateThrottle]

    @extend_schema(
        request=EmailVerificationResendSerializer,
        responses={202: dict},
        description="Sends a new six-digit email verification code.",
    )
    def post(self, request):
        serializer = EmailVerificationResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = resend_email_verification_code(
            email=serializer.validated_data["email"],
        )

        # Не раскрывает, существует ли такой email в системе.
        if result is not None:
            user, code = result
            transaction.on_commit(
                lambda: send_email_verification_code.delay(
                    email=user.email,
                    code=code,
                )
            )

        return Response(
            {
                "detail": (
                    "If this email has an unverified account, "
                    "a new verification code was sent."
                )
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    throttle_classes = [LoginRateThrottle]
    permission_classes = [AllowAny]


class ManagerCreateView(ManagerMutationThrottleMixin, generics.CreateAPIView):
    """Create a manager account from the protected manager web panel."""

    serializer_class = ManagerCreateSerializer
    permission_classes = [IsManager]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            ProfileSerializer(user, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ManagerSellerListView(generics.ListAPIView):
    """Paginated seller directory for managers."""

    serializer_class = ManagerSellerSerializer
    permission_classes = [IsManager]

    def get_queryset(self):
        queryset = (
            User.objects.filter(role=User.Role.SELLER)
            .annotate(product_count=Count("products", distinct=True))
            .order_by("-date_joined")
        )
        search = self.request.query_params.get("search", "").strip()

        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )

        is_active = self.request.query_params.get("is_active")
        if is_active:
            if is_active not in {"true", "false"}:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"is_active": "Use true or false."})

            queryset = queryset.filter(is_active=is_active == "true")

        is_email_verified = self.request.query_params.get("is_email_verified")
        if is_email_verified:
            if is_email_verified not in {"true", "false"}:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"is_email_verified": "Use true or false."})

            queryset = queryset.filter(
                is_email_verified=is_email_verified == "true"
            )

        return queryset


class ManagerSellerConfirmEmailView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=None,
        responses={200: ManagerSellerSerializer},
        description=(
            "Manually confirms a seller email and activates the account. "
            "Use when the verification email did not arrive."
        ),
    )
    def post(self, request, user_id: int):
        seller = get_object_or_404(User, pk=user_id, role=User.Role.SELLER)
        seller = manager_confirm_seller_email(seller=seller)
        seller = (
            User.objects.filter(pk=seller.pk)
            .annotate(product_count=Count("products", distinct=True))
            .get()
        )
        return Response(
            ManagerSellerSerializer(seller, context={"request": request}).data
        )


class ManagerSellerDeleteView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=None,
        responses={202: None, 204: None},
        description=(
            "Hard-deletes a seller. Active marketplace listings are removed "
            "first (OTTO deactivate, Hood/Kaufland delete). The account is "
            "disabled immediately and fully deleted after those jobs finish."
        ),
    )
    def delete(self, request, user_id: int):
        seller = get_object_or_404(User, pk=user_id, role=User.Role.SELLER)
        result = purge_seller(seller=seller, requested_by=request.user)
        if result["deleted"]:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(result, status=status.HTTP_202_ACCEPTED)


class MeView(generics.RetrieveUpdateAPIView):
    # PATCH is the sole update verb. DELETE lets a seller remove their own
    # account; managers use the seller-delete endpoint instead.
    http_method_names = ["get", "patch", "delete", "head", "options"]
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [IsAuthenticated(), IsSeller()]
        return super().get_permissions()

    def get_object(self):
        return self.request.user

    @extend_schema(
        request=None,
        responses={202: None, 204: None},
        description=(
            "Lets a seller delete their own account. Active marketplace "
            "listings are removed first; the account is disabled immediately "
            "and fully deleted after those jobs finish."
        ),
    )
    def delete(self, request, *args, **kwargs):
        result = purge_seller(seller=request.user, requested_by=request.user)
        if result["deleted"]:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(result, status=status.HTTP_202_ACCEPTED)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=LogoutSerializer,
        responses={204: None},
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LoginRateThrottle]

    @extend_schema(
        request=PasswordChangeSerializer,
        responses={204: None},
        description=(
            "Изменяет пароль авторизованного пользователя и отзывает все "
            "его refresh-токены."
        ),
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            request.user.set_password(serializer.validated_data["new_password"])
            request.user.save(update_fields=("password",))

            revoke_refresh_tokens(user=request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRequestRateThrottle]

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses={202: PasswordResetRequestResponseSerializer},
        description=(
            "Отправляет шестизначный код сброса пароля на email. Ответ не "
            "раскрывает, зарегистрирован ли такой пользователь."
        ),
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            result = request_password_reset(
                email=serializer.validated_data["email"],
            )
            if result is not None:
                user, code = result
                transaction.on_commit(
                    lambda: send_password_reset_code.delay(
                        email=user.email,
                        code=code,
                    )
                )

        return Response(
            {
                "detail": (
                    "If an active account uses this email, a reset code was sent."
                )
            },
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetVerifyRateThrottle]

    @extend_schema(
        request=PasswordResetVerifySerializer,
        responses={200: PasswordResetVerifyResponseSerializer},
        description=(
            "Проверяет шестизначный код и возвращает короткоживущий "
            "reset_token для установки нового пароля."
        ),
    )
    def post(self, request):
        serializer = PasswordResetVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reset_token = verify_password_reset_code(
            email=serializer.validated_data["email"],
            code=serializer.validated_data["code"],
        )
        return Response({"reset_token": reset_token}, status=status.HTTP_200_OK)


class PasswordResetCompleteView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetVerifyRateThrottle]

    @extend_schema(
        request=PasswordResetCompleteSerializer,
        responses={204: None},
        description=(
            "Устанавливает новый пароль по reset_token и отзывает все "
            "активные refresh-токены. После этого пользователь входит заново."
        ),
    )
    def post(self, request):
        serializer = PasswordResetCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complete_password_reset(
            reset_token=serializer.validated_data["reset_token"],
            new_password=serializer.validated_data["new_password"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
