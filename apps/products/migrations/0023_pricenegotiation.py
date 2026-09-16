import django.core.validators
import django.db.models.deletion
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("products", "0022_product_seller_change_review"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceNegotiation",
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
                (
                    "currency",
                    models.CharField(
                        choices=[
                            ("TRY", "Turkish lira"),
                            ("EUR", "Euro"),
                            ("USD", "US dollar"),
                        ],
                        max_length=3,
                    ),
                ),
                (
                    "current_unit_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.01"))
                        ],
                    ),
                ),
                (
                    "proposed_unit_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.01"))
                        ],
                    ),
                ),
                ("message", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("superseded", "Superseded"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("seller_comment", models.TextField(blank=True, default="")),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manager",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="price_negotiations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="price_negotiations",
                        to="products.product",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="pricenegotiation",
            index=models.Index(
                fields=["product", "status", "-created_at"],
                name="price_neg_product_status_idx",
            ),
        ),
    ]
