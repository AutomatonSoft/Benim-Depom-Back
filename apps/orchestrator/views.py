from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager, is_manager
from apps.products.models import Product

from .models import MarketplaceJob
from .serializers import MarketplaceJobRequestSerializer, MarketplaceJobSerializer
from .tasks import execute_marketplace_job


class ProductMarketplaceJobCreateView(APIView):
    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(request=MarketplaceJobRequestSerializer, responses={202: MarketplaceJobSerializer})
    def post(self, request, product_pk: int, operation: str):
        if operation not in MarketplaceJob.Operation.values:
            return Response({"detail": "Operation must be search, publish, or update."}, status=status.HTTP_404_NOT_FOUND)
        product = get_object_or_404(Product, pk=product_pk)
        if not is_manager(request.user) and product.owner_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = MarketplaceJobRequestSerializer(data=request.data, context={"operation": operation})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            job = MarketplaceJob.objects.create(
                product=product,
                requested_by=request.user,
                operation=operation,
                requested_channels=data["channels"],
                request_payload={"payloads": data.get("payloads", {}), "accounts": data.get("accounts", {})},
            )
            transaction.on_commit(lambda: execute_marketplace_job.delay(str(job.id)))
        return Response(MarketplaceJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class MarketplaceJobDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses={200: MarketplaceJobSerializer})
    def get(self, request, job_id):
        job = get_object_or_404(MarketplaceJob.objects.select_related("product"), pk=job_id)
        if not is_manager(request.user) and job.product.owner_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        return Response(MarketplaceJobSerializer(job).data)
