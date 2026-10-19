from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ebay", "0003_ebay_draft")]

    operations = [
        migrations.AddField(
            model_name="ebaydraft",
            name="fulfillment_policy_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="ebaydraft",
            name="payment_policy_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="ebaydraft",
            name="return_policy_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="ebaydraft",
            name="merchant_location_key",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
