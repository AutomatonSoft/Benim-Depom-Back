import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0023_pricenegotiation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productvariant",
            name="quantity",
            field=models.PositiveIntegerField(
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
