from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken


from .models import User
from .services import register_user


@extend_schema_serializer(component_name="AuthRegister")
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
    )
    password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False
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
            "is_phone_verified",
            "preferred_language",
            "role",
            "date_joined",
        )
        read_only_fields = (
            "id",
            "username",
            "role",
            "is_phone_verified",
            "date_joined",
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



@extend_schema_serializer(component_name="MobilePhoneVerification")
class FirebasePhoneVerificationSerializer(serializers.Serializer):
    id_token = serializers.CharField(
        write_only=True,
        trim_whitespace=True,
    )
    
