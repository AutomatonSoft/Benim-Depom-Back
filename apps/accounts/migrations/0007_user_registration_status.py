from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_username_not_unique_login_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="registration_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                default="approved",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="registration_rejection_reason",
            field=models.TextField(blank=True),
        ),
    ]