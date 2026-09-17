import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("afterbuy", "0004_sale_manager_actions"),
        ("notifications", "0014_product_sold_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="afterbuy_order_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="notifications",
                to="afterbuy.afterbuyorderitem",
            ),
        ),
    ]
