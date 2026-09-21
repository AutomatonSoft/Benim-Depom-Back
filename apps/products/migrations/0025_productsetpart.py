import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0024_variant_quantity_allow_zero"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductSetPart",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("description", models.TextField()),
                (
                    "width_cm",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=8,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.01")),
                        ],
                    ),
                ),
                (
                    "height_cm",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=8,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.01")),
                        ],
                    ),
                ),
                (
                    "length_cm",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=8,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.01")),
                        ],
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="set_parts",
                        to="products.product",
                    ),
                ),
            ],
            options={
                "ordering": ("position", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="productsetpart",
            constraint=models.UniqueConstraint(
                fields=("product", "position"),
                name="unique_product_set_part_position",
            ),
        ),
    ]