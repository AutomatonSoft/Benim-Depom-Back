from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.services import create_notification
from apps.products.models import Product


class Command(BaseCommand):
    help = "Creates missing manager notifications for products awaiting moderation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create the missing notifications. Without it the command is a dry run.",
        )

    def handle(self, *args, **options):
        products = Product.objects.filter(
            status=Product.Status.SUBMITTED,
        ).select_related("owner")
        managers = list(
            User.objects.filter(
                Q(role__in=(User.Role.MANAGER, User.Role.ADMIN)) | Q(is_superuser=True),
                is_active=True,
            ).distinct()
        )
        created = 0

        for product in products:
            for manager in managers:
                exists = Notification.objects.filter(
                    user=manager,
                    product=product,
                    notification_type=Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
                ).exists()
                if exists:
                    continue
                created += 1
                if options["apply"]:
                    create_notification(
                        user=manager,
                        sender=product.owner,
                        product=product,
                        notification_type=Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
                    )

        verb = "Created" if options["apply"] else "Would create"
        self.stdout.write(f"{verb} {created} moderation notification(s).")
