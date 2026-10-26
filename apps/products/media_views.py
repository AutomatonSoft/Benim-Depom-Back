import mimetypes
from pathlib import Path

from django.core import signing
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .media_uploads import SIGNING_SALT, local_photo_storage, retry_photo_upload
from .models import Product, ProductImage


@require_GET
def serve_staged_photo(request, image_id, kind, signature):
    """Serve a capability-signed local photo; old URLs redirect after cleanup."""
    if kind not in {"original", "thumbnail"}:
        raise Http404
    try:
        signing.Signer(salt=SIGNING_SALT).unsign(f"{image_id}:{kind}:{signature}")
    except signing.BadSignature as exc:
        raise Http404 from exc
    image = get_object_or_404(
        ProductImage.objects.exclude(product__status=Product.Status.ARCHIVED),
        pk=image_id,
    )
    storage = local_photo_storage()
    if image.local_source:
        directory = str(Path(image.local_source).parent)
        optimized = (
            f"{directory}/{'thumbnail' if kind == 'thumbnail' else 'optimized'}.jpg"
        )
        name = optimized if storage.exists(optimized) else image.local_source
        try:
            file = storage.open(name, "rb")
        except FileNotFoundError:
            if image.uploaded_at is None:
                raise Http404
        else:
            response = FileResponse(
                file,
                content_type=mimetypes.guess_type(name)[0]
                or "application/octet-stream",
            )
            response["Cache-Control"] = "private, max-age=60"
            response["X-Content-Type-Options"] = "nosniff"
            return response
    field = image.thumbnail if kind == "thumbnail" and image.thumbnail else image.image
    response = HttpResponseRedirect(field.url)
    response["Cache-Control"] = "private, max-age=60"
    return response


class RetryProductPhotoUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id, owner=request.user)
        retry_photo_upload(product)
        return Response({"image_upload_status": "pending"}, status=202)
