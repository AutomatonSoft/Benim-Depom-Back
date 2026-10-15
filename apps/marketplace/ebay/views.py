from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin

from .models import EbayConnection
from .security import challenge_response, verify_notification
from .services import (
    complete_authorization,
    process_account_deletion,
    start_authorization,
)


class EbayAuthorizeSerializer(serializers.Serializer):
    account = serializers.ChoiceField(choices=EbayConnection.Account.choices)


class EbayAuthorizationStartView(ManagerMutationThrottleMixin, APIView):
    """Позволяет менеджеру начать подключение JV, XL или Depotum к eBay."""

    permission_classes = (IsManager,)

    @extend_schema(
        request=EbayAuthorizeSerializer, responses={200: dict}, tags=["eBay"]
    )
    def post(self, request):
        """Проверяет выбранный аккаунт и возвращает ссылку OAuth-авторизации."""
        serializer = EbayAuthorizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        url = start_authorization(
            account=serializer.validated_data["account"], requested_by=request.user
        )
        return Response(
            {"authorization_url": url, "environment": settings.EBAY_ENVIRONMENT}
        )


class EbayAuthorizationCallbackView(APIView):
    """Принимает перенаправление eBay после согласия или отказа продавца."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = ()

    @extend_schema(
        parameters=[OpenApiParameter("state", required=True), OpenApiParameter("code")],
        responses={200: dict},
        tags=["eBay"],
    )
    def get(self, request):
        """Завершает авторизацию и подтверждает подключение без выдачи токенов."""
        connection = complete_authorization(
            state=request.query_params.get("state", ""),
            code=request.query_params.get("code", ""),
            denied=bool(request.query_params.get("error")),
        )
        response = Response(
            {
                "connected": True,
                "account": connection.account,
                "environment": connection.environment,
            }
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class EbayConnectionListView(APIView):
    """Показывает менеджеру подключения текущего окружения без секретов."""

    permission_classes = (IsManager,)

    @extend_schema(responses={200: dict}, tags=["eBay"])
    def get(self, request):
        connections = EbayConnection.objects.filter(
            environment=settings.EBAY_ENVIRONMENT
        )
        return Response(
            {
                "environment": settings.EBAY_ENVIRONMENT,
                "connections": list(
                    connections.values(
                        "account",
                        "access_token_expires_at",
                        "refresh_token_expires_at",
                        "updated_at",
                    )
                ),
            }
        )


class EbayAccountDeletionView(APIView):
    """Подтверждает адрес уведомлений и принимает сообщения об удалении аккаунта."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = ()

    @extend_schema(
        parameters=[OpenApiParameter("challenge_code", required=True)],
        responses={200: dict},
        tags=["eBay"],
    )
    def get(self, request):
        """Отвечает на проверочный challenge eBay без изменения данных."""
        return Response(
            {
                "challengeResponse": challenge_response(
                    request.query_params.get("challenge_code", "")
                )
            }
        )

    @extend_schema(request=dict, responses={204: None}, tags=["eBay"])
    def post(self, request):
        """Проверяет подпись уведомления и удаляет соответствующее подключение."""
        payload = verify_notification(
            request.body, request.headers.get("X-EBAY-SIGNATURE", "")
        )
        process_account_deletion(payload)
        return Response(status=204)
