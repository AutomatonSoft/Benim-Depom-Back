from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.contact import (
    InvalidContactPhone,
    contact_payload,
    load_contact_phone,
    save_contact_phone,
)
from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin


class WhatsAppContactSerializer(serializers.Serializer):
    phone = serializers.CharField(
        allow_blank=True,
        max_length=32,
        help_text="International WhatsApp number, for example +49 176 12345678.",
    )
    whatsapp_url = serializers.CharField(read_only=True, allow_null=True)


class WhatsAppContactView(ManagerMutationThrottleMixin, APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated(), IsManager()]

    @extend_schema(
        tags=["Contact"],
        summary="Get the public WhatsApp contact number",
        responses={200: WhatsAppContactSerializer},
    )
    def get(self, request):
        return Response(contact_payload(load_contact_phone()))

    @extend_schema(
        tags=["Contact"],
        summary="Update the public WhatsApp contact number",
        request=WhatsAppContactSerializer,
        responses={200: WhatsAppContactSerializer},
    )
    def patch(self, request):
        serializer = WhatsAppContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            phone = save_contact_phone(serializer.validated_data["phone"])
        except InvalidContactPhone as exc:
            return Response(
                {"phone": [str(exc)]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(contact_payload(phone))
