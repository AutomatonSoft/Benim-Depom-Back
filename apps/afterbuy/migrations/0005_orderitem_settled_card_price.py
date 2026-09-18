from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("afterbuy", "0004_sale_manager_actions"),
    ]

    operations = [
        migrations.AddField(
            model_name="afterbuyorderitem",
            name="settled_currency",
            field=models.CharField(blank=True, max_length=3),
        ),
        migrations.AddField(
            model_name="afterbuyorderitem",
            name="settled_unit_price",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True
            ),
        ),
    ]
