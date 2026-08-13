# Generated manually: simplify the product contract without losing old data.

from django.db import migrations, models


def copy_product_type_and_eans(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    EanCode = apps.get_model("ean", "EanCode")

    for product in Product.objects.select_related("product_type").iterator():
        product.product_type_text = product.product_type.name
        assigned_codes = {
            ean.account: ean.code
            for ean in EanCode.objects.filter(product_id=product.id)
        }
        product.ean_jv = assigned_codes.get("jv", "")
        product.ean_xl = assigned_codes.get("xl", "")
        product.save(update_fields=("product_type_text", "ean_jv", "ean_xl"))


class Migration(migrations.Migration):
    dependencies = [
        ("ean", "0001_initial"),
        ("products", "0007_product_deactivation_requested_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="product_type_text",
            field=models.CharField(default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="product",
            name="ean_jv",
            field=models.CharField(blank=True, default="", max_length=14),
        ),
        migrations.AddField(
            model_name="product",
            name="ean_xl",
            field=models.CharField(blank=True, default="", max_length=14),
        ),
        migrations.RunPython(copy_product_type_and_eans, migrations.RunPython.noop),
        migrations.RemoveField(model_name="product", name="product_type"),
        migrations.RenameField(
            model_name="product", old_name="product_type_text", new_name="product_type"
        ),
        migrations.AlterField(
            model_name="product",
            name="product_type",
            field=models.CharField(db_index=True, max_length=255),
        ),
    ]
