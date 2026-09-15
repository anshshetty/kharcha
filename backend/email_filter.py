"""Broad received-mail scanning; transaction retention is decided locally."""

POLICY_VERSION = 2
BLOCKED_LABELS = frozenset({"SPAM", "TRASH", "SENT", "DRAFT"})


def received_query(window):
    return window + " -in:spam -in:trash -in:sent -in:drafts"


def is_received_message(message):
    if not isinstance(message, dict):
        return False
    labels = message.get("labelIds", [])
    return (
        isinstance(labels, list)
        and all(isinstance(label, str) for label in labels)
        and not BLOCKED_LABELS.intersection(labels)
    )
