from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0011_product_price_and_currency"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="unit_price",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=12,
                validators=[MinValueValidator(Decimal("0.01"))],
            ),
        ),
    ]
