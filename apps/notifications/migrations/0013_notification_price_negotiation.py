from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0012_price_negotiation_types"),
        ("products", "0023_pricenegotiation"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="price_negotiation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="notifications",
                to="products.pricenegotiation",
            ),
        ),
    ]
