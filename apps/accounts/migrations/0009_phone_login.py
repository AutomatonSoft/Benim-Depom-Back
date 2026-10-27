import re

import django.contrib.auth.validators
from django.db import migrations, models
from django.db.models.functions import Lower


def normalize_existing_phones(apps, schema_editor):
    """Stop on conflicting legacy data instead of changing account ownership."""
    User = apps.get_model("accounts", "User")
    users = User.objects.using(schema_editor.connection.alias)
    seen = {}
    normalized = []
    for user in users.all().iterator():
        phone = re.sub(r"[\s().-]", "", (user.phone or "").strip())
        if phone.startswith("00"):
            phone = "+" + phone[2:]
        if not phone:
            normalized.append((user.pk, None))
            continue
        if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", phone):
            raise RuntimeError(
                f"Correct the invalid phone for account {user.pk} before migrating."
            )
        phone = "+" + phone.lstrip("+")
        if phone in seen:
            raise RuntimeError(
                f"Accounts {seen[phone]} and {user.pk} share a phone. Resolve this before migrating."
            )
        seen[phone] = user.pk
        normalized.append((user.pk, phone))
    for user_id, phone in normalized:
        users.filter(pk=user_id).update(phone=phone)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_activate_pending_registrations")]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(max_length=32, null=True, blank=True),
        ),
        migrations.RunPython(normalize_existing_phones, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(max_length=32, null=True, blank=True, unique=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(max_length=254, db_index=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                max_length=150,
                validators=[django.contrib.auth.validators.UnicodeUsernameValidator()],
                help_text="Display name. Not unique; sellers authenticate using phone.",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("email"),
                condition=models.Q(role__in=["manager", "admin"])
                | models.Q(is_staff=True)
                | models.Q(is_superuser=True),
                name="unique_staff_email",
            ),
        ),
    ]
