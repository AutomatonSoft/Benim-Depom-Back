from apps.accounts.models import User

SUPPORTED_LANGUAGES = frozenset(User.Language.values)

_TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "product_approved": {
        "en": ("Product approval", 'Your product "{name}" was approved.'),
        "ru": ("Товар одобрен", "Ваш товар «{name}» одобрен."),
        "de": ("Produktfreigabe", 'Ihr Produkt "{name}" wurde freigegeben.'),
        "tr": ("Ürün onayı", '"{name}" ürününüz onaylandı.'),
    },
    "product_changes_approved": {
        "en": (
            "Product changes approved",
            "Your changes to '{name}' were approved.",
        ),
        "ru": (
            "Изменения товара одобрены",
            "Ваши изменения товара «{name}» одобрены.",
        ),
        "de": (
            "Produktänderungen genehmigt",
            "Ihre Änderungen an '{name}' wurden genehmigt.",
        ),
        "tr": (
            "Ürün değişiklikleri onaylandı",
            "'{name}' ürünündeki değişiklikleriniz onaylandı.",
        ),
    },
    "product_rejected": {
        "en": ("Product rejected", "Your product '{name}' was rejected."),
        "ru": ("Товар отклонён", "Ваш товар «{name}» отклонён."),
        "de": ("Produkt abgelehnt", "Ihr Produkt '{name}' wurde abgelehnt."),
        "tr": ("Ürün reddedildi", "'{name}' ürününüz reddedildi."),
    },
    "product_changes_rejected": {
        "en": ("Product changes rejected", "Your changes to '{name}' were rejected."),
        "ru": (
            "Изменения товара отклонены",
            "Ваши изменения товара «{name}» отклонены.",
        ),
        "de": (
            "Produktänderungen abgelehnt",
            "Ihre Änderungen an '{name}' wurden abgelehnt.",
        ),
        "tr": (
            "Ürün değişiklikleri reddedildi",
            "'{name}' ürünündeki değişiklikleriniz reddedildi.",
        ),
    },
    "product_availability_reminder": {
        "en": ("Product availability", 'Do you still have "{name}" available?'),
        "ru": ("Наличие товара", "Товар «{name}» всё ещё в наличии?"),
        "de": ("Produktverfügbarkeit", 'Haben Sie "{name}" noch verfügbar?'),
        "tr": ("Ürün stok durumu", '"{name}" ürününüz hâlâ mevcut mu?'),
    },
    "product_confirmation_available": {
        "en": (
            "Product is available",
            "{seller_name} confirmed '{name}' is still available.",
        ),
        "ru": (
            "Товар в наличии",
            "{seller_name} подтвердил, что «{name}» всё ещё в наличии.",
        ),
        "de": (
            "Produkt ist verfügbar",
            "{seller_name} hat bestätigt, dass '{name}' noch verfügbar ist.",
        ),
        "tr": (
            "Ürün stokta",
            "{seller_name}, '{name}' ürününün hâlâ stokta olduğunu onayladı.",
        ),
    },
    "product_confirmation_unavailable": {
        "en": (
            "Product is not available",
            "{seller_name} confirmed '{name}' is not available.",
        ),
        "ru": (
            "Товара нет в наличии",
            "{seller_name} подтвердил, что «{name}» нет в наличии.",
        ),
        "de": (
            "Produkt nicht verfügbar",
            "{seller_name} hat bestätigt, dass '{name}' nicht verfügbar ist.",
        ),
        "tr": (
            "Ürün stokta yok",
            "{seller_name}, '{name}' ürününün stokta olmadığını onayladı.",
        ),
    },
    "product_submitted_for_review": {
        "en": (
            "New product awaiting review",
            "{seller_name} submitted '{name}' for moderation.",
        ),
        "ru": (
            "Новый товар на модерации",
            "{seller_name} отправил «{name}» на модерацию.",
        ),
        "de": (
            "Neues Produkt wartet auf Prüfung",
            "{seller_name} hat '{name}' zur Prüfung eingereicht.",
        ),
        "tr": (
            "İnceleme bekleyen yeni ürün",
            "{seller_name}, '{name}' ürününü incelemeye gönderdi.",
        ),
    },
    "product_change_requested": {
        "en": (
            "Seller wants to change a product",
            "{seller_name} requested changes to '{name}'. "
            "Open the product to compare current and new values.",
        ),
        "ru": (
            "Продавец хочет изменить товар",
            "{seller_name} запросил изменения товара «{name}». "
            "Откройте карточку, чтобы сравнить текущие и новые значения.",
        ),
        "de": (
            "Verkäufer möchte ein Produkt ändern",
            "{seller_name} hat Änderungen an '{name}' angefordert. "
            "Öffnen Sie das Produkt, um aktuelle und neue Werte zu vergleichen.",
        ),
        "tr": (
            "Satıcı ürünü değiştirmek istiyor",
            "{seller_name}, '{name}' ürününde değişiklik istedi. "
            "Mevcut ve yeni değerleri karşılaştırmak için ürünü açın.",
        ),
    },
    "product_withdrawn_from_review": {
        "en": (
            "Seller withdrew a product from review",
            "{seller_name} withdrew '{name}' from moderation. "
            "Reload the product before continuing.",
        ),
        "ru": (
            "Продавец отозвал товар с модерации",
            "{seller_name} отозвал «{name}» с модерации. "
            "Перед продолжением обновите карточку товара.",
        ),
        "de": (
            "Verkäufer hat Produktprüfung zurückgezogen",
            "{seller_name} hat '{name}' aus der Prüfung zurückgezogen. "
            "Laden Sie das Produkt neu, bevor Sie fortfahren.",
        ),
        "tr": (
            "Satıcı ürünü incelemeden çekti",
            "{seller_name}, '{name}' ürününü incelemeden çekti. "
            "Devam etmeden önce ürünü yenileyin.",
        ),
    },
    "product_deactivation_requested": {
        "en": (
            "Deactivation requested",
            "Seller requested deactivation for '{name}'.",
        ),
        "ru": (
            "Запрос на деактивацию",
            "Продавец запросил деактивацию товара «{name}».",
        ),
        "de": (
            "Deaktivierung angefordert",
            "Der Verkäufer hat die Deaktivierung von '{name}' angefordert.",
        ),
        "tr": (
            "Devre dışı bırakma talebi",
            "Satıcı '{name}' ürününün devre dışı bırakılmasını istedi.",
        ),
    },
    "price_negotiation_offer": {
        "en": ("Price proposal", "{message}"),
        "ru": ("Предложение цены", "{message}"),
        "de": ("Preisvorschlag", "{message}"),
        "tr": ("Fiyat teklifi", "{message}"),
    },
    "price_negotiation_accepted": {
        "en": (
            "Price accepted",
            "{seller_name} accepted {price} {currency} for '{name}'.",
        ),
        "ru": (
            "Цена принята",
            "{seller_name} принял цену {price} {currency} для «{name}».",
        ),
        "de": (
            "Preis akzeptiert",
            "{seller_name} hat {price} {currency} für '{name}' akzeptiert.",
        ),
        "tr": (
            "Fiyat kabul edildi",
            "{seller_name}, '{name}' için {price} {currency} fiyatını kabul etti.",
        ),
    },
    "price_negotiation_rejected": {
        "en": (
            "Price declined",
            "{seller_name} declined {price} {currency} for '{name}'.",
        ),
        "ru": (
            "Цена отклонена",
            "{seller_name} отклонил цену {price} {currency} для «{name}».",
        ),
        "de": (
            "Preis abgelehnt",
            "{seller_name} hat {price} {currency} für '{name}' abgelehnt.",
        ),
        "tr": (
            "Fiyat reddedildi",
            "{seller_name}, '{name}' için {price} {currency} fiyatını reddetti.",
        ),
    },
    "product_sold": {
        "en": (
            "Product sold in Afterbuy",
            "'{name}' was sold in Afterbuy ({marketplace}).\n"
            "Quantity: {qty_sold} pcs.\n"
            "Time: {sold_at}\n\n"
            "Update the warehouse quantity on the product card and notify the seller.",
        ),
        "ru": (
            "Товар купили в Afterbuy",
            "«{name}» купили в Afterbuy ({marketplace}).\n"
            "Количество: {qty_sold} шт.\n"
            "Время: {sold_at}\n\n"
            "Измените количество на складе в карточке товара и уведомите продавца.",
        ),
        "de": (
            "Produkt in Afterbuy verkauft",
            "'{name}' wurde in Afterbuy ({marketplace}) gekauft.\n"
            "Menge: {qty_sold} Stk.\n"
            "Zeit: {sold_at}\n\n"
            "Ändern Sie die Lagermenge in der Produktkarte und benachrichtigen Sie den Verkäufer.",
        ),
        "tr": (
            "Ürün Afterbuy'da satıldı",
            "'{name}' Afterbuy'da ({marketplace}) satıldı.\n"
            "Adet: {qty_sold}\n"
            "Zaman: {sold_at}\n\n"
            "Ürün kartında depo miktarını güncelleyin ve satıcıyı bilgilendirin.",
        ),
    },
    "product_sold_seller": {
        "en": (
            "Your product was sold",
            "Your product was sold.\n\n"
            "Product: {name}\n"
            "Time: {sold_at}\n"
            "Sold: {qty_sold} pcs.",
        ),
        "ru": (
            "Ваш товар купили",
            "Ваш товар купили.\n\n"
            "Товар: {name}\n"
            "Время: {sold_at}\n"
            "Купили: {qty_sold} шт.",
        ),
        "de": (
            "Ihr Produkt wurde gekauft",
            "Ihr Produkt wurde gekauft.\n\n"
            "Produkt: {name}\n"
            "Zeit: {sold_at}\n"
            "Gekauft: {qty_sold} Stk.",
        ),
        "tr": (
            "Ürününüz satıldı",
            "Ürününüz satıldı.\n\n"
            "Ürün: {name}\n"
            "Zaman: {sold_at}\n"
            "Satılan: {qty_sold} adet",
        ),
    },
    "push_fallback": {
        "en": ("Marketplace", "You have a new notification."),
        "ru": ("Marketplace", "У вас новое уведомление."),
        "de": ("Marketplace", "Sie haben eine neue Benachrichtigung."),
        "tr": ("Marketplace", "Yeni bir bildiriminiz var."),
    },
}

_TYPE_KEYS = {
    "product_approved": "product_approved",
    "product_rejected": "product_rejected",
    "product_confirmation": "product_confirmation_available",
    "product_availability_reminder": "product_availability_reminder",
    "product_submitted_for_review": "product_submitted_for_review",
    "product_withdrawn_from_review": "product_withdrawn_from_review",
    "product_change_requested": "product_change_requested",
    "product_deactivation_requested": "product_deactivation_requested",
    "price_negotiation_offer": "price_negotiation_offer",
    "price_negotiation_response": "price_negotiation_accepted",
    "product_sold": "product_sold",
}


def normalize_language(value: str | None) -> str:
    language = (value or "").strip().casefold()
    if language in SUPPORTED_LANGUAGES:
        return language
    return User.Language.RUSSIAN


def render_notification_copy(
    *,
    key: str,
    language: str | None,
    name: str = "",
    seller_name: str = "",
    message: str = "",
    price: str = "",
    currency: str = "",
    sold_at: str = "",
    card_price: str = "",
    qty_sold: str = "",
    marketplace: str = "",
) -> tuple[str, str]:
    lang = normalize_language(language)
    pack = _TEMPLATES.get(key) or _TEMPLATES["push_fallback"]
    title, body = pack.get(lang) or pack[User.Language.RUSSIAN]
    values = {
        "name": name or "",
        "seller_name": seller_name or "",
        "message": message or "",
        "price": price or "",
        "currency": currency or "",
        "sold_at": sold_at or "",
        "card_price": card_price or "",
        "qty_sold": qty_sold or "",
        "marketplace": marketplace or "",
    }
    return title.format(**values), body.format(**values)


def copy_key_for_type(notification_type: str, *, copy_key: str = "") -> str:
    if copy_key:
        return copy_key
    return _TYPE_KEYS.get(notification_type, "")
