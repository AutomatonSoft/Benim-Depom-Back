from rest_framework import serializers

from apps.catalog.models import Category, Color, Material, ProductType

from .models import Product, ProductImage, ProductVariant
from .services import create_product, update_product


class ProductVariantSerializer(serializers.ModelSerializer):
    color = serializers.PrimaryKeyRelatedField(
        queryset=Color.objects.filter(is_active=True),
    )
    material = serializers.PrimaryKeyRelatedField(
        queryset=Material.objects.filter(is_active=True),
    )
    color_name = serializers.CharField(source="color.name", read_only=True)
    material_name = serializers.CharField(source="material.name", read_only=True)

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "color",
            "color_name",
            "material",
            "material_name",
            "width_cm",
            "height_cm",
            "length_cm",
            "quantity",
        )
        read_only_fields = ("id",)


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = (
            "id",
            "image",
            "processed_image",
            "position",
            "is_primary",
            "processing_status",
        )


class ProductSerializer(serializers.ModelSerializer):
    product_type = serializers.PrimaryKeyRelatedField(
        queryset=ProductType.objects.filter(is_active=True),
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    variants = ProductVariantSerializer(many=True, required=False)
    images = ProductImageSerializer(many=True, read_only=True)
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "owner",
            "title",
            "product_type",
            "category",
            "status",
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
        )

    def get_total_quantity(self, product):
        return sum(variant.quantity for variant in product.variants.all())

    def validate(self, attrs):
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