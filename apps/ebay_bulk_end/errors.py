class BulkEndError(Exception):
    pass


class EBayTransportError(BulkEndError):
    pass


class EBayRateLimitError(EBayTransportError):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("eBay временно ограничил частоту запросов.")


class OwnershipMismatchError(BulkEndError):
    pass


class ManagedListingError(BulkEndError):
    pass
