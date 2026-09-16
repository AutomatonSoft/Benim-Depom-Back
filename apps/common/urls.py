from django.urls import path

from apps.common.contact_views import WhatsAppContactView

app_name = "common"

urlpatterns = [
    path(
        "contact/whatsapp/",
        WhatsAppContactView.as_view(),
        name="contact-whatsapp",
    ),
]
