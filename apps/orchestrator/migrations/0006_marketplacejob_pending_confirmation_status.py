from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orchestrator", "0005_marketplacelistingconfiguration"),
    ]

    operations = [
        migrations.AlterField(
            model_name="marketplacejob",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("pending_confirmation", "Pending confirmation"),
                    ("succeeded", "Succeeded"),
                    ("partial", "Partial"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="queued",
                max_length=24,
            ),
        ),
    ]
