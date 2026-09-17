from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("afterbuy", "0003_remove_channel_stock"),
    ]

    operations = [
        migrations.AddField(
            model_name="afterbuysalenotification",
            name="seller_notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="afterbuysalenotification",
            name="stock_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
