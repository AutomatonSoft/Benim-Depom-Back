from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0016_productimage_idle_processing_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="listing_price_eur_override",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                validators=[MinValueValidator(Decimal("0.01"))],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="pricing_overrides",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]



class Migration(migrations.Migration):

    dependencies = [
        ("products", "0016_productimage_idle_processing_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="listing_price_eur_override",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="pricing_overrides",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
