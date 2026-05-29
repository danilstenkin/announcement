def compute_display_status(
    status: str,
    publish_confirmed: bool,
    pub_statuses: list[str],
) -> str:
    """Derive the UI status badge from ticket status + publish confirmation +
    the statuses of the announcement's publications.

    Precedence is intentional:
      PUBLISHED > AGREED(scheduled) > ON_APPROVAL > OVERDUE > NO_DATE > IN_PROGRESS.
    """
    # legacy APPROVED tickets are pre-feature closed/published tickets
    if status in ("PUBLISHED", "APPROVED"):
        return "PUBLISHED"

    if status == "AGREED":
        if "SCHEDULED" in pub_statuses:
            return "AGREED"
        if "CANCELED" in pub_statuses:
            return "OVERDUE"

    if status == "ON_APPROVAL":
        return "ON_APPROVAL"

    # date was confirmed, the scheduled publication was canceled, none active now
    if publish_confirmed and "SCHEDULED" not in pub_statuses and "CANCELED" in pub_statuses:
        return "OVERDUE"

    if not publish_confirmed:
        return "NO_DATE"

    if status in ("IN_REVIEW", "REVISION"):
        return "IN_PROGRESS"

    return "NO_DATE"
