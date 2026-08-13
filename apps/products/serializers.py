from drf_spectacular.utils import (
    extend_schema_field,
    extend_schema_serializer,
)
from rest_framework import serializers

from apps.catalog.models import Category

from .models import (
    Product,
    ProductGeneratedImage,
    ProductImage,
    ProductVariant,
)
from .services import create_product, update_product
from apps.common.permissions import is_manager
from apps.ean.serializers import EanCodeSerializer


@extend_schema_serializer(component_name="ProductsVariant")
class ProductVariantSerializer(serializers.ModelSerializer):
    color_hex = serializers.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$",
        max_length=7,
        error_messages={
            "invalid": "Use a hexadecimal color in the #RRGGBB format.",
        },
    )
    materials = serializers.ListField(
        child=serializers.CharField(max_length=100, trim_whitespace=True),
        min_length=1,
        max_length=2,
    )

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "color_hex",
            "materials",
            "width_cm",
            "height_cm",
            "length_cm",
            "quantity",
        )
        read_only_fields = ("id",)

    def validate_color_hex(self, value):
        return value.upper()

    def validate_materials(self, values):
        normalized = [value.strip() for value in values]

        if any(not value for value in normalized):
            raise serializers.ValidationError(
                "Material names must not be empty."
            )

        if len({value.casefold() for value in normalized}) != len(normalized):
            raise serializers.ValidationError(
                "Material names must be unique within one variant."
            )

        return normalized


@extend_schema_serializer(component_name="ProductsGeneratedImage")
class ProductGeneratedImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductGeneratedImage
        fields = (
            "id",
            "mode",
            "image",
            "created_at",
        )
        read_only_fields = fields


@extend_schema_serializer(component_name="ProductsImage")
class ProductImageSerializer(serializers.ModelSerializer):

    generated_images = ProductGeneratedImageSerializer(
        many=True,
        read_only=True,
    )
    class Meta:
        model = ProductImage
        fields = (
            "id",
            "image",
            "processed_image",
            "position",
            "is_primary",
            "processing_status",
            "processing_error",
            "processing_result",
            "generated_images",
        )
        read_only_fields = (
            "id",
            "image",
            "processed_image",
            "position",
            "is_primary",
            "processing_status",
            "processing_error",
            "processing_result",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        if request and not is_manager(request.user):
            for field in (
                "processed_image",
                "processing_status",
                "processing_error",
                "processing_result",
                "generated_images",
            ):
                data.pop(field, None)

        return data


@extend_schema_serializer(component_name="ProductsProduct")
class ProductSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(max_length=255, trim_whitespace=True)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    variants = ProductVariantSerializer(many=True, required=False)
    images = ProductImageSerializer(many=True, read_only=True)
    total_quantity = serializers.SerializerMethodField()
    ean_codes = EanCodeSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "owner",
            "title",
            "product_type",
            "category",
            "status",
            "is_available",
            "approved_at",
            "availability_confirmed_at",
            "deactivation_requested_at",
            "deactivated_at",
            "ean_codes",
            "variants",
            "images",
            "total_quantity",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "owner",
            "status",
            "images",
            "total_quantity",
            "created_at",
            "updated_at",
            "is_available",
            "approved_at",
            "availability_confirmed_at",
            "deactivation_requested_at",
            "deactivated_at",
            "ean_codes",
        )

    @extend_schema_field(serializers.IntegerField)
    def get_total_quantity(self, product) -> int:
        return sum(variant.quantity for variant in product.variants.all())

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        if request and not is_manager(request.user):
            data.pop("ean_codes", None)

        return data

    def validate(self, attrs):
        product_type = attrs.get("product_type")
        if product_type is not None and not product_type.strip():
            raise serializers.ValidationError(
                {"product_type": "Product type must not be empty."}
            )

        variants = attrs.get("variants")

        if self.instance is None and not variants:
            raise serializers.ValidationError(
                {"variants": "A product must contain at least one variant."}
            )

        if variants is not None and not variants:
            raise serializers.ValidationError(
                {"variants": "At least one variant is required."}
            )

        return attrs

    def create(self, validated_data):
        variants_data = validated_data.pop("variants")

        return create_product(
            owner=self.context["request"].user,
            data=validated_data,
            variants_data=variants_data,
        )

    def update(self, instance, validated_data):
        variants_data = validated_data.pop("variants", None)

        return update_product(
            product=instance,
            data=validated_data,
            variants_data=variants_data,
        )

@extend_schema_serializer(component_name="ProductsAvailability")
class ProductAvailabilitySerializer(serializers.Serializer):
    is_available = serializers.BooleanField()

@extend_schema_serializer(component_name="ProductsImageUpload")
class ProductImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()
    is_primary = serializers.BooleanField(default=False)

    def validate_image(self, image):
        max_size = 10 * 1024 * 1024

        allowed_content_types = {
            "image/jpeg",
            "image/png",
            "image/webp",
        }

        if image.size > max_size:
            raise serializers.ValidationError(
                "Image size must not exceed 10 MB."
            )

        content_type = getattr(image, "content_type", None)
        if content_type and content_type not in allowed_content_types:
            raise serializers.ValidationError(
                "Allowed image formats: JPEG, PNG, WEBP"
            )

        return image


@extend_schema_serializer(component_name="ProductsImageReorder")
class ProductImageReorderSerializer(serializers.Serializer):
    image_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty = False,
    )

    def validate_image_ids(self, image_ids):
        if len(image_ids) != len(set(image_ids)):
            raise serializers.ValidationError(
                "Image identifiers must be unique"
            )


        return image_ids

    
