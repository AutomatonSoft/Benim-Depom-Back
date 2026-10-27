from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from . import views
from .models import User
from .serializers import (
    EmailVerificationResendSerializer,
    EmailVerificationSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
    PhoneField,
    ProfileSerializer,
    RegisterSerializer,
    _validate_unique_phone,
)


def unique_email(value, instance=None):
    email = value.strip().lower()
    if (
        User.objects.filter(email__iexact=email)
        .exclude(pk=getattr(instance, "pk", None))
        .exists()
    ):
        raise serializers.ValidationError("A user with this email already exists.")
    return email


@extend_schema_serializer(component_name="AuthLoginV1")
class EmailLoginSerializer(TokenObtainPairSerializer):
    username_field = "email"

    def validate(self, attrs):
        """Authenticate legacy clients only when email identifies one account."""
        matches = list(User.objects.filter(email__iexact=attrs["email"].strip())[:2])
        user = matches[0] if len(matches) == 1 else None
        if user is None:
            User().set_password(attrs["password"])
        if (
            user is None
            or not user.check_password(attrs["password"])
            or not user.is_active
        ):
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )
        self.user = user
        refresh = self.get_token(user)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}


@extend_schema_serializer(component_name="AuthRegisterV1")
class RegisterSerializerV1(RegisterSerializer):
    phone = PhoneField(required=False, allow_blank=True)

    def validate_email(self, value):
        return unique_email(value)


@extend_schema_serializer(component_name="AuthProfileV1")
class ProfileSerializerV1(ProfileSerializer):
    def validate_phone(self, value):
        return _validate_unique_phone(value, self.instance)

    def validate_email(self, value):
        return unique_email(value, self.instance)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["phone"] = data["phone"] or ""
        return data


@extend_schema_serializer(component_name="EmailVerificationV1")
class EmailVerificationSerializerV1(EmailVerificationSerializer):
    phone = PhoneField(required=False)


@extend_schema_serializer(component_name="EmailVerificationResendV1")
class EmailVerificationResendSerializerV1(EmailVerificationResendSerializer):
    phone = PhoneField(required=False)


@extend_schema_serializer(component_name="AuthPasswordResetRequestV1")
class PasswordResetRequestSerializerV1(PasswordResetRequestSerializer):
    email = serializers.EmailField(required=True)
    phone = PhoneField(required=False)


@extend_schema_serializer(component_name="AuthPasswordResetVerifyV1")
class PasswordResetVerifySerializerV1(PasswordResetVerifySerializer):
    email = serializers.EmailField(required=True)
    phone = PhoneField(required=False)


class RegisterViewV1(views.RegisterView):
    serializer_class = RegisterSerializerV1
    profile_serializer_class = ProfileSerializerV1


class LoginViewV1(views.LoginView):
    serializer_class = EmailLoginSerializer


class MeViewV1(views.MeView):
    serializer_class = ProfileSerializerV1


class EmailVerificationViewV1(views.EmailVerificationView):
    serializer_class = EmailVerificationSerializerV1
    profile_serializer_class = ProfileSerializerV1


class EmailVerificationResendViewV1(views.EmailVerificationResendView):
    serializer_class = EmailVerificationResendSerializerV1


class PasswordResetRequestViewV1(views.PasswordResetRequestView):
    serializer_class = PasswordResetRequestSerializerV1
    report_reset_errors = False


class PasswordResetVerifyViewV1(views.PasswordResetVerifyView):
    serializer_class = PasswordResetVerifySerializerV1
