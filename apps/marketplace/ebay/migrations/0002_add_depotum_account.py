from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ebay", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="ebayconnection",
            name="account",
            field=models.CharField(
                choices=[("jv", "JV"), ("xl", "XL"), ("depotum", "Depotum")],
                max_length=7,
            ),
        ),
        migrations.AlterField(
            model_name="ebayoauthattempt",
            name="account",
            field=models.CharField(
                choices=[("jv", "JV"), ("xl", "XL"), ("depotum", "Depotum")],
                max_length=7,
            ),
        ),
    ]
