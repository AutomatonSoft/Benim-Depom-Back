from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .services import register_user


@extend_schema_serializer(component_name="AuthRegister")
class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)

    class Meta:
        model = User
        fields = (
            "username",
            "password",
            "password_confirm",
            "email",
            "first_name",
            "last_name",
            "phone",
            "preferred_language",
        )

        extra_kwargs = {
            "first_name": {"required": False},
            "last_name": {"required": False},
            "phone": {"required": False},
        }

    def validate(self, attrs):
        password = attrs["password"]
        password_confirm = attrs.pop("password_confirm")

        if password != password_confirm:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match"}
            )

        user = User(
            username=attrs.get("username"),
            email=attrs.get("email", ""),
        )
        validate_password(password, user)

        return attrs

    def create(self, validated_data):
        return register_user(data=validated_data)

    def validate_email(self, value):
        email = value.strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")

        return email


@extend_schema_serializer(component_name="WebManagerCreate")
class ManagerCreateSerializer(serializers.ModelSerializer):
    """Manager-only user creation. This serializer never accepts a role."""

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "password",
            "password_confirm",
            "email",
            "first_name",
            "last_name",
            "phone",
            "preferred_language",
        )
        extra_kwargs = {
            "email": {"required": False},
            "first_name": {"required": False},
            "last_name": {"required": False},
            "phone": {"required": False},
        }

    def validate(self, attrs):
        password = attrs["password"]
        password_confirm = attrs.pop("password_confirm")

        if password != password_confirm:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )

        user = User(
            username=attrs.get("username"),
            email=attrs.get("email", ""),
        )
        validate_password(password, user)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(
            password=password,
            role=User.Role.MANAGER,
            **validated_data,
        )


@extend_schema_serializer(component_name="AuthProfile")
class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "preferred_language",
            "role",
            "date_joined",
            "is_email_verified",
        )
        read_only_fields = (
            "id",
            "username",
            "role",
            "date_joined",
            "is_email_verified",
        )


@extend_schema_serializer(component_name="AuthLogout")
class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def save(self, **kwargs):
        try:
            token = RefreshToken(self.validated_data["refresh"])
            token.blacklist()

        except TokenError as exc:
            raise serializers.ValidationError(
                {"refresh": "Invalid or expired refresh token"}
            ) from exc


@extend_schema_serializer(component_name="AuthPasswordChange")
class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    new_password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        user = self.context["request"].user

        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError(
                {"current_password": "Current password is incorrect."}
            )

        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "Passwords do not match."}
            )

        if user.check_password(attrs["new_password"]):
            raise serializers.ValidationError(
                {"new_password": "Choose a different password."}
            )

        validate_password(attrs["new_password"], user)
        return attrs


@extend_schema_serializer(component_name="AuthPasswordResetRequest")
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


@extend_schema_serializer(component_name="AuthPasswordResetVerify")
class PasswordResetVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6, trim_whitespace=True)

    def validate_email(self, value):
        return value.strip().lower()

    def validate_code(self, value):
        if not value.isdecimal():
            raise serializers.ValidationError(
                "Reset code must contain exactly 6 digits."
            )
        return value


@extend_schema_serializer(component_name="AuthPasswordResetComplete")
class PasswordResetCompleteSerializer(serializers.Serializer):
    reset_token = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    new_password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "Passwords do not match."}
            )
        return attrs


@extend_schema_serializer(component_name="AuthPasswordResetRequestResponse")
class PasswordResetRequestResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


@extend_schema_serializer(component_name="AuthPasswordResetVerifyResponse")
class PasswordResetVerifyResponseSerializer(serializers.Serializer):
    reset_token = serializers.CharField(read_only=True)


@extend_schema_serializer(component_name="EmailVerification")
class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(
        min_length=6,
        max_length=6,
        trim_whitespace=True,
    )

    def validate_code(self, value):
        if not value.isdecimal():
            raise serializers.ValidationError(
                "Verification code must contain exactly 6 digits."
            )

        return value


@extend_schema_serializer(component_name="EmailVerificationResend")
class EmailVerificationResendSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()
