from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.common.permissions import IsManager
from apps.common.throttles import (
    EmailVerificationRateThrottle,
    EmailVerificationResendRateThrottle,
    LoginRateThrottle,
    ManagerMutationThrottleMixin,
    RegistrationRateThrottle,
)

from .serializers import (
    EmailVerificationResendSerializer,
    EmailVerificationSerializer,
    LogoutSerializer,
    ManagerCreateSerializer,
    ProfileSerializer,
    RegisterSerializer,
)
from .services import (
    issue_email_verification_code,
    resend_email_verification_code,
    verify_email_code,
)
from .tasks import send_email_verification_code


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


class MeView(generics.RetrieveUpdateAPIView):
    # The profile is edited partially. Do not expose PUT as a duplicate
    # full-replacement API that clients do not need.
    http_method_names = ["get", "patch", "head", "options"]
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


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
