from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("afterbuy", "0005_orderitem_settled_card_price"),
        ("ebay", "0008_listing_id_history"),
    ]

    operations = [
        migrations.AlterField(
            model_name=name,
            name="marketplace",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("otto", "OTTO"),
                    ("hood", "Hood"),
                    ("kaufland", "Kaufland"),
                    ("ebay", "eBay"),
                    ("other", "Other"),
                ],
                default="other",
                db_index=True,
            ),
        )
        for name in ("afterbuyorder", "afterbuyorderitem")
    ]
