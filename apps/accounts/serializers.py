import unicodedata

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .phone import normalize_phone
from .services import register_user


def _canonical_preferred_language(value):
    if not isinstance(value, str):
        return value
    return User.canonical_language(value) or value


def _validate_password_value(*, password, user, field_name: str) -> None:
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field_name: list(exc.messages)}) from exc


@extend_schema_serializer(component_name="AuthLogin")
class LoginSerializer(TokenObtainPairSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = False
        self.fields["email"] = serializers.EmailField(required=False)

    def validate(self, attrs):
        if bool(attrs.get("phone")) == bool(attrs.get("email")):
            raise serializers.ValidationError("Provide either phone or staff email.")
        self.user = authenticate(
            request=self.context.get("request"),
            phone=attrs.get("phone"),
            email=attrs.get("email"),
            password=attrs["password"],
        )
        if self.user is None:
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )
        refresh = self.get_token(self.user)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc


def _validate_unique_phone(value, instance=None):
    if not value:
        return None
    if (
        User.objects.filter(phone=value)
        .exclude(pk=getattr(instance, "pk", None))
        .exists()
    ):
        raise serializers.ValidationError(
            "An account with this phone number already exists."
        )
    return value


def _validate_staff_email(value, instance=None):
    email = value.strip().lower()
    if (
        User.objects.filter(
            Q(role__in=[User.Role.MANAGER, User.Role.ADMIN])
            | Q(is_staff=True)
            | Q(is_superuser=True),
            email__iexact=email,
        )
        .exclude(pk=getattr(instance, "pk", None))
        .exists()
    ):
        raise serializers.ValidationError("A user with this email already exists.")
    return email


class AccountSerializer(serializers.ModelSerializer):
    def save(self, **kwargs):
        try:
            with transaction.atomic():
                return super().save(**kwargs)
        except IntegrityError:
            # A concurrent registration or profile edit can win after validation.
            phone = self.validated_data.get("phone")
            if phone:
                _validate_unique_phone(phone, self.instance)
            raise


class PersonNameField(serializers.CharField):
    """Accept personal names of 2–40 Unicode letters without separators."""

    def __init__(self, **kwargs):
        super().__init__(min_length=2, max_length=40, trim_whitespace=False, **kwargs)

    def to_internal_value(self, data):
        value = unicodedata.normalize("NFC", super().to_internal_value(data))
        if not value.isalpha():
            raise serializers.ValidationError("Names must contain letters only.")
        return value


@extend_schema_serializer(component_name="AuthRegister")
class RegisterSerializer(AccountSerializer):
    email = serializers.EmailField(required=True)
    phone = PhoneField(required=True)
    first_name = PersonNameField()
    last_name = PersonNameField()
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
        _validate_password_value(password=password, user=user, field_name="password")

        return attrs

    def create(self, validated_data):
        return register_user(data=validated_data)

    def validate_email(self, value):
        return value.strip().lower()

    def validate_phone(self, value):
        return _validate_unique_phone(value)

    def validate_preferred_language(self, value):
        return _canonical_preferred_language(value)


@extend_schema_serializer(component_name="WebManagerCreate")
class ManagerCreateSerializer(AccountSerializer):
    """Manager-only user creation. This serializer never accepts a role."""

    phone = PhoneField(required=False, allow_blank=True)

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
            "email": {"required": True},
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
        _validate_password_value(password=password, user=user, field_name="password")
        return attrs

    def validate_email(self, value):
        return _validate_staff_email(value)

    def validate_phone(self, value):
        return _validate_unique_phone(value)

    def validate_preferred_language(self, value):
        return _canonical_preferred_language(value)

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(
            password=password,
            role=User.Role.MANAGER,
            is_email_verified=True,
            **validated_data,
        )


@extend_schema_serializer(component_name="PreferredLanguage")
class PreferredLanguageSerializer(serializers.Serializer):
    language = serializers.ChoiceField(choices=User.Language.choices)

    def to_internal_value(self, data):
        if isinstance(data, dict) and isinstance(data.get("language"), str):
            language = User.canonical_language(data["language"]) or data["language"]
            data = {**data, "language": language}
        return super().to_internal_value(data)


@extend_schema_serializer(component_name="AuthProfile")
class ProfileSerializer(AccountSerializer):
    phone = PhoneField(required=False, allow_blank=True)
    first_name = PersonNameField(required=False)
    last_name = PersonNameField(required=False)
    product_count = serializers.SerializerMethodField()

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
            "product_count",
        )
        read_only_fields = (
            "id",
            "username",
            "role",
            "date_joined",
            "product_count",
        )

    def validate_preferred_language(self, value):
        return _canonical_preferred_language(value)

    def validate_phone(self, value):
        if not value and self.instance.role == User.Role.SELLER:
            raise serializers.ValidationError("This field may not be blank.")
        return _validate_unique_phone(value, self.instance)

    def validate_email(self, value):
        if (
            self.instance.role in {User.Role.MANAGER, User.Role.ADMIN}
            or self.instance.is_staff
            or self.instance.is_superuser
        ):
            return _validate_staff_email(value, self.instance)
        return value.strip().lower()

    @extend_schema_field(serializers.IntegerField())
    def get_product_count(self, user) -> int:
        annotated = getattr(user, "product_count", None)
        if annotated is not None:
            return int(annotated)
        return user.products.count()


@extend_schema_serializer(component_name="ManagerSeller")
class ManagerSellerSerializer(ProfileSerializer):
    """Seller directory row for managers, including activation state."""

    class Meta(ProfileSerializer.Meta):
        fields = ProfileSerializer.Meta.fields + (
            "is_active",
            "is_email_verified",
        )
        read_only_fields = ProfileSerializer.Meta.read_only_fields + (
            "is_active",
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

        _validate_password_value(
            password=attrs["new_password"],
            user=user,
            field_name="new_password",
        )
        return attrs


@extend_schema_serializer(component_name="AuthPasswordResetRequest")
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    phone = PhoneField(required=True)

    def validate(self, attrs):
        if not attrs.get("phone") and not attrs.get("email"):
            raise serializers.ValidationError({"phone": "This field is required."})
        return attrs

    def validate_email(self, value):
        return value.strip().lower()


@extend_schema_serializer(component_name="AuthPasswordResetVerify")
class PasswordResetVerifySerializer(PasswordResetRequestSerializer):
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
    phone = PhoneField(required=True)
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
    phone = PhoneField(required=True)

    def validate_email(self, value):
        return value.strip().lower()
