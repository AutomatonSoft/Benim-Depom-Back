from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0034_productgeneratedimage_regen_status")]

    operations = [
        migrations.AddField(
            model_name="product",
            name="cbm_override",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=9,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.001"))
                ],
            ),
        ),
    ]
