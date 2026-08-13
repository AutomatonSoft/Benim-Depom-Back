from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .services import request_product_image_processing
from .models import Product, ProductImage
from apps.common.permissions import IsManager, IsSeller, is_manager

from .filters import filter_products
from .permissions import CanAccessProduct
from .serializers import (
    ProductImageReorderSerializer,
    ProductImageSerializer,
    ProductImageUploadSerializer,
    ProductSerializer,
    ProductAvailabilitySerializer,
)
from rest_framework.parsers import FormParser, MultiPartParser
from .services import (
    delete_product_image,
    make_product_image_primary,
    reorder_product_images,
    upload_product_image,
    confirm_product_availability,
    deactivate_product,
    request_product_deactivation,
    withdraw_product_submission,
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
            Product.objects.select_related("owner", "category")
            .prefetch_related(
                "variants",
                "images",
                "images__generated_images",
                "ean_codes",
            )
        )

        if not is_manager(self.request.user):
            queryset = queryset.filter(owner=self.request.user)

        queryset = queryset.exclude(status=Product.Status.ARCHIVED)

        return filter_products(
            queryset=queryset,
            query_params=self.request.query_params,
        )


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, CanAccessProduct]

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner", "category")
            .prefetch_related(
                "variants",
                "images",
                "images__generated_images",
                "ean_codes",
            )
        )

        if not is_manager(self.request.user):
            queryset = queryset.filter(owner=self.request.user)

        return queryset.exclude(status=Product.Status.ARCHIVED)

    def perform_destroy(self, instance):
        if (
            not is_manager(self.request.user)
            and instance.status not in {
                Product.Status.DRAFT,
                Product.Status.REJECTED,
            }
        ):
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {
                    "detail": (
                        "Use withdraw for submitted products or deactivate "
                        "for approved products."
                    )
                }
            )

        instance.status = Product.Status.ARCHIVED
        instance.save(update_fields=("status", "updated_at"))


class ProductImageUploadView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

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

    @extend_schema(request=None, responses={204: None})
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

    @extend_schema(request=None, responses={200: ProductImageSerializer})
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


class ProductImageProcessView(APIView):
    permission_classes = [IsManager]

    @extend_schema(request=None, responses={202: ProductImageSerializer})
    def post(self, request, product_pk: int, image_pk: int):
        product = get_object_or_404(
            Product.objects.exclude(status=Product.Status.ARCHIVED),
            pk=product_pk,
        )
        image = get_object_or_404(
            ProductImage,
            id=image_pk,
            product=product,
        )
        image = request_product_image_processing(
            product=product,
            image=image,
        )
        return Response(
            ProductImageSerializer(
                image,
                context={"request": request},
            ).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ProductDeactivateView(APIView):
    """Seller submits a deactivation request; a manager confirms it later."""
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(request=None, responses={202: ProductSerializer})
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product.objects.exclude(status=Product.Status.ARCHIVED),
            pk=product_pk,
            owner=request.user,
        )
        from apps.accounts.models import User
        from apps.notifications.models import Notification
        from apps.notifications.services import create_notification

        product = request_product_deactivation(product=product)
        managers = User.objects.filter(
            is_staff=True
        ) | User.objects.filter(
            role__in=(User.Role.MANAGER, User.Role.ADMIN)
        )
        for manager in managers.distinct():
            create_notification(
                user=manager,
                sender=request.user,
                product=product,
                notification_type=Notification.Type.PRODUCT_DEACTIVATION_REQUESTED,
                title="Deactivation requested",
                body=f"Seller requested deactivation for '{product.title}'.",
                data={"product_id": product.id},
            )

        return Response(
            ProductSerializer(product, context={"request": request}).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ProductWithdrawView(APIView):
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(request=None, responses={204: None})
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product,
            pk=product_pk,
            owner=request.user,
        )
        withdraw_product_submission(product=product)
        return Response(status=status.HTTP_204_NO_CONTENT)

    
