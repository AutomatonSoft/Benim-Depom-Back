from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import extend_schema

from .serializers import (
    LogoutSerializer,
    ManagerCreateSerializer,
    ProfileSerializer,
    RegisterSerializer,
    FirebasePhoneVerificationSerializer
)
from .firebase_auth import get_verified_phone_from_id_token
from .services import verify_user_phone
from apps.common.permissions import IsManager


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]


class ManagerCreateView(generics.CreateAPIView):
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

class PhoneVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=FirebasePhoneVerificationSerializer,
        responses={200: ProfileSerializer},
    )
    def post(self, request):
        serializer = FirebasePhoneVerificationSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        phone_number = get_verified_phone_from_id_token(
            id_token=serializer.validated_data["id_token"],
        )

        user = verify_user_phone(
            user=request.user,
            phone_number=phone_number,
        )

        return Response(
            ProfileSerializer(
                user,
                context={"request": request},
            ).data
        )
    
class MeView(generics.RetrieveUpdateAPIView):
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
