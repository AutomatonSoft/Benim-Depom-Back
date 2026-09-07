from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("moderation", "0002_moderationdecision_returned_to_review"),
    ]

    operations = [
        migrations.AlterField(
            model_name="moderationdecision",
            name="decision",
            field=models.CharField(
                choices=[
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("returned_to_review", "Returned to review"),
                    ("withdrawn", "Withdrawn"),
                ],
                max_length=20,
            ),
        ),
    ]
