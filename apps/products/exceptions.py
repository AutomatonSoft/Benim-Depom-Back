from rest_framework import status
from rest_framework.exceptions import APIException


class ProductVersionConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "product_version_conflict"
    default_detail = "This product was changed by the seller. Reload it and try again."
