import django.contrib.auth.validators
from django.db import migrations, models


def fill_empty_emails(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(email=""):
        user.email = f"user-{user.pk}@placeholder.invalid"
        user.save(update_fields=("email",))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_user_password_reset_fields"),
    ]

    operations = [
        migrations.RunPython(fill_empty_emails, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="user",
            name="accounts_user_unique_nonempty_email",
        ),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                help_text="Display name. Not unique; authentication uses email.",
                max_length=150,
                unique=False,
                validators=[django.contrib.auth.validators.UnicodeUsernameValidator()],
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(max_length=254, unique=True),
        ),
    ]
