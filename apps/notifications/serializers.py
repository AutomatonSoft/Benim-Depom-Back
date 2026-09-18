from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from apps.common.permissions import is_manager

from .models import DeviceToken, Notification
from .services import extract_seller_comment

NOTIFICATION_CATEGORY_CHOICES = (
    ("review", "Review"),
    ("availability", "Availability"),
    ("price", "Price negotiations"),
    ("afterbuy", "Afterbuy"),
    ("outgoing", "Outgoing"),
)

REVIEW_TYPES = (
    Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
    Notification.Type.PRODUCT_CHANGE_REQUESTED,
)

AVAILABILITY_TYPES = (
    Notification.Type.PRODUCT_CONFIRMATION,
    Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
    Notification.Type.PRODUCT_DEACTIVATION_REQUESTED,
)

PRICE_TYPES = (Notification.Type.PRICE_NEGOTIATION_RESPONSE,)

AFTERBUY_TYPES = (Notification.Type.PRODUCT_SOLD,)

MANAGER_SENT_TYPES = (
    Notification.Type.MANAGER_MESSAGE,
    Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
    Notification.Type.PRODUCT_DEACTIVATION_REQUESTED,
    Notification.Type.PRICE_NEGOTIATION_OFFER,
    Notification.Type.PRODUCT_APPROVED,
    Notification.Type.PRODUCT_REJECTED,
)


@extend_schema_serializer(component_name="NotificationsDeviceToken")
class DeviceTokenSerializer(serializers.ModelSerializer):
    token = serializers.CharField(write_only=True)

    class Meta:
        model = DeviceToken
        fields = (
            "id",
            "token",
            "platform",
            "is_active",
            "last_seen_at",
        )
        read_only_fields = (
            "id",
            "is_active",
            "last_seen_at",
        )

    def create(self, validated_data):
        user = self.context["request"].user

        device_token, _ = DeviceToken.objects.update_or_create(
            token=validated_data["token"],
            defaults={
                "user": user,
                "platform": validated_data["platform"],
                "is_active": True,
            },
        )

        return device_token


@extend_schema_serializer(component_name="NotificationsDeviceDeactivate")
class DeviceTokenDeactivateSerializer(serializers.Serializer):
    token = serializers.CharField()


@extend_schema_serializer(component_name="NotificationsNotificationFilter")
class NotificationFilterSerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)
    category = serializers.ChoiceField(
        choices=NOTIFICATION_CATEGORY_CHOICES,
        required=False,
    )
    is_read = serializers.BooleanField(required=False, allow_null=True, default=None)
    notification_type = serializers.ChoiceField(
        choices=Notification.Type.choices,
        required=False,
    )


@extend_schema_serializer(component_name="NotificationsNotification")
class NotificationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(
        source="product.id",
        read_only=True,
        allow_null=True,
    )
    product_title = serializers.SerializerMethodField()
    product_status = serializers.SerializerMethodField()
    seller_username = serializers.SerializerMethodField()
    seller_email = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    sender_username = serializers.SerializerMethodField()
    sender_email = serializers.SerializerMethodField()
    sender_name = serializers.SerializerMethodField()
    manager_username = serializers.SerializerMethodField()
    manager_email = serializers.SerializerMethodField()
    manager_name = serializers.SerializerMethodField()
    seller_comment = serializers.SerializerMethodField()
    price_negotiation_id = serializers.SerializerMethodField()
    price_negotiation_status = serializers.SerializerMethodField()
    proposed_unit_price = serializers.SerializerMethodField()
    proposed_currency = serializers.SerializerMethodField()
    price_accepted = serializers.SerializerMethodField()
    responded_at = serializers.SerializerMethodField()
    afterbuy_marketplace = serializers.SerializerMethodField()
    afterbuy_account = serializers.SerializerMethodField()
    afterbuy_qty_sold = serializers.SerializerMethodField()
    afterbuy_stock_synced = serializers.SerializerMethodField()
    afterbuy_seller_notified = serializers.SerializerMethodField()

    sender_id = serializers.IntegerField(
        source="sender.id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Notification
        fields = (
            "id",
            "notification_type",
            "product_id",
            "product_title",
            "product_status",
            "seller_username",
            "seller_email",
            "seller_name",
            "is_read",
            "created_at",
            "read_at",
            "responded_at",
            "sender_id",
            "sender_username",
            "sender_email",
            "sender_name",
            "manager_username",
            "manager_email",
            "manager_name",
            "title",
            "body",
            "seller_comment",
            "price_negotiation_id",
            "price_negotiation_status",
            "proposed_unit_price",
            "proposed_currency",
            "price_accepted",
            "afterbuy_marketplace",
            "afterbuy_account",
            "afterbuy_qty_sold",
            "afterbuy_stock_synced",
            "afterbuy_seller_notified",
        )
        read_only_fields = fields

    def _seller(self, notification: Notification):
        product = notification.product
        if product is not None:
            owner = getattr(product, "owner", None)
            if owner is not None:
                return owner
        return getattr(notification, "user", None)

    def _person_name(self, person) -> str | None:
        if person is None:
            return None
        full = f"{person.first_name or ''} {person.last_name or ''}".strip()
        return full or None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_product_title(self, notification) -> str | None:
        if notification.product_id is None:
            return None
        return notification.product.title

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_product_status(self, notification) -> str | None:
        if notification.product_id is None:
            return None
        return notification.product.status

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_seller_username(self, notification) -> str | None:
        seller = self._seller(notification)
        return seller.username if seller is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_seller_email(self, notification) -> str | None:
        seller = self._seller(notification)
        return seller.email if seller is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_seller_name(self, notification) -> str | None:
        return self._person_name(self._seller(notification))

    def _manager(self, notification: Notification):
        sender = notification.sender
        if sender is not None and is_manager(sender):
            return sender
        negotiation = self._related_price_negotiation(notification)
        if negotiation is not None:
            manager = getattr(negotiation, "manager", None)
            if manager is not None:
                return manager
        product = notification.product
        if product is None:
            return None
        related = getattr(product, "manager_sent_notifications", None)
        if related is None:
            return None
        for item in related:
            if item.id == notification.id:
                continue
            other = item.sender
            if other is not None and is_manager(other):
                return other
        return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_manager_username(self, notification) -> str | None:
        manager = self._manager(notification)
        return manager.username if manager is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_manager_email(self, notification) -> str | None:
        manager = self._manager(notification)
        return manager.email if manager is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_manager_name(self, notification) -> str | None:
        return self._person_name(self._manager(notification))

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_sender_username(self, notification) -> str | None:
        sender = notification.sender
        return sender.username if sender is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_sender_email(self, notification) -> str | None:
        sender = notification.sender
        return sender.email if sender is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_sender_name(self, notification) -> str | None:
        return self._person_name(notification.sender)

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_seller_comment(self, notification) -> str:
        negotiation = self._related_price_negotiation(notification)
        if negotiation is not None and negotiation.seller_comment.strip():
            return negotiation.seller_comment.strip()
        return extract_seller_comment(notification.body or "")

    def _related_price_negotiation(self, notification):
        if notification.notification_type not in {
            Notification.Type.PRICE_NEGOTIATION_OFFER,
            Notification.Type.PRICE_NEGOTIATION_RESPONSE,
        }:
            return None
        linked = getattr(notification, "price_negotiation", None)
        if linked is not None:
            return linked
        if notification.product_id is None:
            return None
        negotiations = list(notification.product.price_negotiations.all())
        if not negotiations:
            return None
        cutoff = notification.created_at
        earlier = [item for item in negotiations if item.created_at <= cutoff]
        pool = earlier or negotiations
        return max(pool, key=lambda item: (item.created_at, item.id))

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_price_negotiation_id(self, notification) -> int | None:
        negotiation = self._related_price_negotiation(notification)
        return negotiation.id if negotiation is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_price_negotiation_status(self, notification) -> str | None:
        negotiation = self._related_price_negotiation(notification)
        return negotiation.status if negotiation is not None else None

    @extend_schema_field(
        serializers.DecimalField(
            max_digits=12, decimal_places=2, allow_null=True, coerce_to_string=True
        )
    )
    def get_proposed_unit_price(self, notification):
        negotiation = self._related_price_negotiation(notification)
        if negotiation is None:
            return None
        return f"{negotiation.proposed_unit_price:.2f}"

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_proposed_currency(self, notification) -> str | None:
        negotiation = self._related_price_negotiation(notification)
        return negotiation.currency if negotiation is not None else None

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_price_accepted(self, notification) -> bool | None:
        negotiation = self._related_price_negotiation(notification)
        if negotiation is None:
            return None
        from apps.products.models import PriceNegotiation

        if negotiation.status == PriceNegotiation.Status.ACCEPTED:
            return True
        if negotiation.status == PriceNegotiation.Status.REJECTED:
            return False
        return None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_responded_at(self, notification):
        if notification.responded_at is not None:
            return notification.responded_at
        negotiation = self._related_price_negotiation(notification)
        if negotiation is not None:
            return negotiation.responded_at
        return None

    def _afterbuy_sale(self, notification: Notification):
        item = getattr(notification, "afterbuy_order_item", None)
        if item is None:
            return None
        return getattr(item, "sale_notification", None)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_afterbuy_marketplace(self, notification) -> str | None:
        item = getattr(notification, "afterbuy_order_item", None)
        if item is None:
            return None
        return item.marketplace

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_afterbuy_account(self, notification) -> str | None:
        item = getattr(notification, "afterbuy_order_item", None)
        if item is None:
            return None
        order = getattr(item, "order", None)
        return order.account if order is not None else None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_afterbuy_qty_sold(self, notification) -> int | None:
        item = getattr(notification, "afterbuy_order_item", None)
        if item is None:
            return None
        return item.quantity

    @extend_schema_field(serializers.BooleanField())
    def get_afterbuy_stock_synced(self, notification) -> bool:
        sale = self._afterbuy_sale(notification)
        return bool(sale is not None and sale.stock_synced_at is not None)

    @extend_schema_field(serializers.BooleanField())
    def get_afterbuy_seller_notified(self, notification) -> bool:
        sale = self._afterbuy_sale(notification)
        return bool(sale is not None and sale.seller_notified_at is not None)


@extend_schema_serializer(component_name="NotificationsSummary")
class NotificationSummarySerializer(serializers.Serializer):
    unread_total = serializers.IntegerField()
    all = serializers.IntegerField()
    review = serializers.IntegerField()
    availability = serializers.IntegerField()
    price = serializers.IntegerField()
    outgoing = serializers.IntegerField()
    unread_review = serializers.IntegerField()
    unread_availability = serializers.IntegerField()
    unread_price = serializers.IntegerField()
    afterbuy = serializers.IntegerField()
    unread_afterbuy = serializers.IntegerField()
    unread_outgoing = serializers.IntegerField()


@extend_schema_serializer(component_name="WebManagerProductNotification")
class ManagerProductNotificationSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
    )
    body = serializers.CharField(max_length=1500)

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message text cannot be empty.")
        return value
