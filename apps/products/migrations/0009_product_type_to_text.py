# Convert the legacy ProductType foreign key into the seller-supplied text.

from django.db import migrations, models


def copy_product_type_name(apps, schema_editor):
    Product = apps.get_model("products", "Product")

    for product in Product.objects.select_related("product_type").iterator():
        product.product_type_text = product.product_type.name
        product.save(update_fields=("product_type_text",))


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0008_product_ean_jv_product_ean_xl"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="product_type_text",
            field=models.CharField(default="", max_length=255),
            preserve_default=False,
        ),
        migrations.RunPython(copy_product_type_name, migrations.RunPython.noop),
        migrations.RemoveField(model_name="product", name="product_type"),
        migrations.RenameField(
            model_name="product",
            old_name="product_type_text",
            new_name="product_type",
        ),
        migrations.AlterField(
            model_name="product",
            name="product_type",
            field=models.CharField(db_index=True, max_length=255),
        ),
    ]
