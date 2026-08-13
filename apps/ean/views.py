from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager

from .models import EanCode
from .serializers import EanCodeSerializer, EanImportSerializer
from .services import get_ean_summary, import_ean_codes


class ManagerEanImportView(APIView):
    permission_classes = [IsManager]

    @extend_schema(request=EanImportSerializer, responses={201: dict})
    def post(self, request):
        serializer = EanImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = import_ean_codes(
            account=serializer.validated_data["account"],
            raw_codes=serializer.validated_data["codes"],
            imported_by=request.user,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class ManagerEanSummaryView(APIView):
    permission_classes = [IsManager]

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(get_ean_summary())


class ManagerEanListView(generics.ListAPIView):
    permission_classes = [IsManager]
    serializer_class = EanCodeSerializer

    def get_queryset(self):
        queryset = EanCode.objects.select_related("product")
        account = self.request.query_params.get("account")
        is_assigned = self.request.query_params.get("is_assigned")

        if account in EanCode.Account.values:
            queryset = queryset.filter(account=account)

        if is_assigned == "true":
            queryset = queryset.filter(product__isnull=False)
        elif is_assigned == "false":
            queryset = queryset.filter(product__isnull=True)

        return queryset
