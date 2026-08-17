# Generated manually: price is nullable only while legacy local products
# are removed. Migration 0012 makes it mandatory at the database level.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0010_product_otto_attributes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="currency",
            field=models.CharField(
                choices=[
                    ("TRY", "Turkish lira"),
                    ("EUR", "Euro"),
                    ("USD", "US dollar"),
                ],
                default="TRY",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="unit_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
            ),
        ),
    ]
