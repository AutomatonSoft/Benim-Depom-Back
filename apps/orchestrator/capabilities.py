MARKETPLACE_OPERATIONS = {
    "hood": {
        "search",
        "publish",
        "update",
        "delete",
    },
    "kaufland": {
        "search",
        "publish",
        "update",
        "delete",
        "activate",
        "deactivate",
    },
    "otto": {
        "search",
        "publish",
        "update",
        "activate",
        "deactivate",
    },
}


def supports_operation(*, marketplace: str, operation: str) -> bool:
    return operation in MARKETPLACE_OPERATIONS.get(
        marketplace,
        set(),
    )
