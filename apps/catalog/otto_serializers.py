from rest_framework import serializers


class OttoCategoryGroupSerializer(serializers.Serializer):
    category_group_id = serializers.IntegerField()
    category_group = serializers.CharField()
    category_count = serializers.IntegerField()


class OttoCategorySerializer(serializers.Serializer):
    category_id = serializers.IntegerField(source="categoryId")
    category_group_id = serializers.IntegerField()
    category_group = serializers.CharField()
    name = serializers.CharField()


class OttoCategoryAttributeSerializer(serializers.Serializer):
    attribute_id = serializers.IntegerField(source="attributeId")
    attribute_key = serializers.CharField(source="attributeKey")
    name = serializers.CharField()
    type = serializers.CharField()
    attribute_group = serializers.CharField(source="attributeGroup")
    description = serializers.CharField(allow_blank=True)
    relevance = serializers.CharField()
    feature_relevance = serializers.ListField(
        child=serializers.CharField(),
        source="featureRelevance",
    )
    multi_value = serializers.BooleanField(source="multiValue")
    unit = serializers.CharField(allow_blank=True)
    unit_display_name = serializers.CharField(
        source="unitDisplayName",
        allow_null=True,
    )
    allowed_values = serializers.ListField(source="allowedValues")