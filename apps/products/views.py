from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ProductImage
from .permissions import CanAccessProduct, IsSeller, is_manager
from .serializers import (
    ProductImageReorderSerializer,
    ProductImageSerializer,
    ProductImageUploadSerializer,
    ProductSerializer,
    ProductAvailabilitySerializer,

)
from .services import (
    delete_product_image,
    make_product_image_primary,
    reorder_product_images,
    upload_product_image,
    confirm_product_availability,
)


def get_editable_product_for_user(*, user, product_id: int) -> Product:
    return get_object_or_404(
        Product.objects.filter(
            pk=product_id,
            owner=user,
            status__in=(
                Product.Status.DRAFT,
                Product.Status.REJECTED,
            ),
        )
    )


class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsSeller()]

        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner", "product_type", "category")
            .prefetch_related(
                "variants__color",
                "variants__material",
                "images",
            )
        )

        if not is_manager(self.request.user):
            queryset = queryset.filter(owner=self.request.user)

        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = queryset.filter(status=status_value)

        return queryset.exclude(status=Product.Status.ARCHIVED)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, CanAccessProduct]

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner", "product_type", "category")
            .prefetch_related(
                "variants__color",
                "variants__material",
                "images",
            )
        )

        if not is_manager(self.request.user):
            queryset = queryset.filter(owner=self.request.user)

        return queryset.exclude(status=Product.Status.ARCHIVED)

    def perform_destroy(self, instance):
        instance.status = Product.Status.ARCHIVED
        instance.save(update_fields=("status", "updated_at"))


class ProductImageUploadView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        request=ProductImageUploadSerializer,
        responses={201: ProductImageSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_editable_product_for_user(
            user=request.user,
            product_id=product_pk,
        )

        serializer = ProductImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        image = upload_product_image(
            product=product,
            image_file=serializer.validated_data["image"],
            is_primary=serializer.validated_data["is_primary"],
        )

        return Response(
            ProductImageSerializer(image, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ProductImageDeleteView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    def delete(self, request, product_pk: int, image_pk: int):
        product = get_editable_product_for_user(
            user=request.user,
            product_id=product_pk,
        )
        image = get_object_or_404(
            ProductImage,
            pk=image_pk,
            product=product,
        )

        delete_product_image(product=product, image=image)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductImagePrimaryView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(responses={200: ProductImageSerializer})
    def post(self, request, product_pk: int, image_pk: int):
        product = get_editable_product_for_user(
            user=request.user,
            product_id=product_pk,
        )
        image = get_object_or_404(
            ProductImage,
            pk=image_pk,
            product=product,
        )

        image = make_product_image_primary(
            product=product,
            image=image,
        )

        return Response(
            ProductImageSerializer(image, context={"request": request}).data
        )


class ProductImageReorderView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(request=ProductImageReorderSerializer, responses={204: None})
    def post(self, request, product_pk: int):
        product = get_editable_product_for_user(
            user=request.user,
            product_id=product_pk,
        )

        serializer = ProductImageReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reorder_product_images(
            product=product,
            image_ids=serializer.validated_data["image_ids"],
        )

        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductAvailabilityView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        request=ProductAvailabilitySerializer,
        responses={200: ProductSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product,
            pk=product_pk,
            owner=request.user,
        )

        serializer = ProductAvailabilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = confirm_product_availability(
            product=product,
            is_available=serializer.validated_data["is_available"],
        )

        return Response(
            ProductSerializer(product, context={"request": request}).data
        )