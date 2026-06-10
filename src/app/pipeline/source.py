"""Single source of truth for mapping a sender email to its source system."""

_SOURCE_MAP = {
    "sd_info@fortebank.com": "ServiceDesk",
    "komek@fortebank.com": "komek",
    "aaaskarova@fortebank.com": "ServiceDesk",
    "dastenkin@fortebank.com": "RetailInfo",
    "retailinfo2@fortebank.com": "RetailInfo",
}


def resolve_source(sender_email: str | None) -> str:
    """Resolve the source system from a sender email. Unknown/empty -> 'other'."""
    if not sender_email:
        return "other"
    return _SOURCE_MAP.get(sender_email.strip().lower(), "other")
