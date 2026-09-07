from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0019_product_pending_catalog_changes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("withdrawn", "Withdrawn"),
                    ("archived", "Archived"),
                    ("deactivated", "Deactivated"),
                ],
                db_index=True,
                default="draft",
                max_length=20,
            ),
        ),
    ]
