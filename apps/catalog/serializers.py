from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from .models import Color, Material, ProductType


@extend_schema_serializer(component_name="CatalogProductType")
class ProductTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductType
        fields = (
            "id", 
            "name",
            "is_active",
            "sort_order",
        )


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = (
            "id", 
            "name",
            "is_active",
            "sort_order",
        )


class ColorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Color
        fields = (
            "id",
            "name",
            "hex_code",
            "is_active",
            "sort_order",
        )
