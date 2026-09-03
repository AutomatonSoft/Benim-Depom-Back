from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("moderation", "0001_initial"),
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
                ],
                max_length=20,
            ),
        ),
    ]
