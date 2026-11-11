class BulkEndError(Exception):
    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class EBayTransportError(BulkEndError):
    pass


class ReportStorageError(BulkEndError):
    pass


class SnapshotOwnershipLost(BulkEndError):
    pass


class EBayRateLimitError(EBayTransportError):
    def __init__(self, retry_after, *, diagnostics=None):
        self.retry_after = retry_after
        super().__init__(
            "eBay временно ограничил частоту запросов.", diagnostics=diagnostics
        )


class OwnershipMismatchError(BulkEndError):
    pass


class ManagedListingError(BulkEndError):
    pass


class IdentifierConflictError(BulkEndError):
    pass
