from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product
from apps.products.permissions import IsSeller, is_manager
from apps.products.serializers import ProductSerializer

from .models import ModerationDecision
from .serializers import (
    ApproveProductSerializer,
    ModerationDecisionSerializer,
    RejectProductSerializer,
)
from .services import (
    approve_product,
    reject_product,
    submit_product_for_moderation,
)


class IsManager(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and is_manager(
            request.user
        )


class SubmitProductView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(responses={200: ProductSerializer})
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product,
            pk=product_pk,
            owner=request.user,
        )

        product = submit_product_for_moderation(product=product)

        return Response(
            ProductSerializer(product, context={"request": request}).data
        )


class ProductModerationHistoryView(generics.ListAPIView):
    serializer_class = ModerationDecisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        product_queryset = Product.objects.all()

        if not is_manager(self.request.user):
            product_queryset = product_queryset.filter(
                owner=self.request.user
            )

        product = get_object_or_404(
            product_queryset,
            pk=self.kwargs["product_pk"],
        )

        return (
            ModerationDecision.objects.filter(product=product)
            .select_related("manager")
            .order_by("-created_at")
        )


class ManagerProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsManager]

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner", "product_type", "category")
            .prefetch_related(
                "variants__color",
                "variants__material",
                "images",
            )
            .exclude(status=Product.Status.ARCHIVED)
        )

        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = queryset.filter(status=status_value)

        owner_id = self.request.query_params.get("owner_id")
        if owner_id:
            queryset = queryset.filter(owner_id=owner_id)

        return queryset


class ManagerApproveProductView(APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=ApproveProductSerializer,
        responses={200: ProductSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)

        serializer = ApproveProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = approve_product(
            product=product,
            manager=request.user,
            comment=serializer.validated_data.get("comment", ""),
        )

        return Response(
            ProductSerializer(product, context={"request": request}).data
        )


class ManagerRejectProductView(APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=RejectProductSerializer,
        responses={200: ProductSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)

        serializer = RejectProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = reject_product(
            product=product,
            manager=request.user,
            comment=serializer.validated_data["comment"],
        )

        return Response(
            ProductSerializer(product, context={"request": request}).data
        )