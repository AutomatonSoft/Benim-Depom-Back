# Generated manually for the simplified mobile variant contract.

from django.core.validators import RegexValidator
from django.db import migrations, models


def copy_catalog_values_to_variant(apps, schema_editor):
    ProductVariant = apps.get_model("products", "ProductVariant")

    for variant in ProductVariant.objects.select_related("color", "material"):
        color = variant.color
        material = variant.material
        variant.color_hex = (color.hex_code if color else "#000000").upper()
        variant.materials = [material.name] if material else []
        variant.save(update_fields=("color_hex", "materials"))


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0005_product_deactivated_at_and_generated_upload_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="productvariant",
            name="color_hex",
            field=models.CharField(default="#000000", max_length=7),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productvariant",
            name="materials",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(
            copy_catalog_values_to_variant,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="color",
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="material",
        ),
        migrations.AlterField(
            model_name="productvariant",
            name="color_hex",
            field=models.CharField(
                db_index=True,
                max_length=7,
                validators=[
                    RegexValidator(
                        message="Use a hexadecimal color in the #RRGGBB format.",
                        regex="^#[0-9A-Fa-f]{6}$",
                    )
                ],
            ),
        ),
    ]
