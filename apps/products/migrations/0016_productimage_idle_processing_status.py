from django.db import migrations, models


def mark_unstarted_images_idle(apps, schema_editor):
    ProductImage = apps.get_model("products", "ProductImage")
    ProductImage.objects.filter(
        processing_status="pending",
        processing_claimed_at__isnull=True,
    ).update(processing_status="idle")


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0015_exchangerate_product_warehouse_city"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productimage",
            name="processing_status",
            field=models.CharField(
                choices=[
                    ("idle", "Idle"),
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("result_received", "Result received"),
                ],
                default="idle",
                max_length=20,
            ),
        ),
        migrations.RunPython(mark_unstarted_images_idle, migrations.RunPython.noop),
    ]
