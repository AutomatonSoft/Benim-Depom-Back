from django.db import migrations, models


def reclassify_under_review_as_submitted(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    Product.objects.filter(status="under_review").update(status="submitted")


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0017_product_listing_formula_overrides"),
    ]

    operations = [
        migrations.RunPython(
            reclassify_under_review_as_submitted,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="product",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("archived", "Archived"),
                    ("deactivated", "Deactivated"),
                ],
                db_index=True,
                default="draft",
                max_length=20,
            ),
        ),
    ]
