from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("afterbuy", "0002_channel_stock_and_followup"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="afterbuyorderitem",
            name="followup_reconciled_at",
        ),
        migrations.DeleteModel(
            name="AfterbuyChannelStock",
        ),
    ]
