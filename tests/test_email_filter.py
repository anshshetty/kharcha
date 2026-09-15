import pytest

from backend.email_filter import BLOCKED_LABELS, is_received_message, received_query


def test_received_query_has_no_sender_subject_or_category_filter():
    assert received_query("after:100 before:200") == (
        "after:100 before:200 -in:spam -in:trash -in:sent -in:drafts"
    )


@pytest.mark.parametrize(
    "labels",
    [
        [],
        ["INBOX"],
        ["CATEGORY_PROMOTIONS"],
        ["CATEGORY_SOCIAL"],
        ["CATEGORY_FORUMS"],
        ["INBOX", "custom-label"],
    ],
)
def test_received_labels_are_scanned(labels):
    assert is_received_message({"id": "synthetic", "labelIds": labels})


@pytest.mark.parametrize("label", sorted(BLOCKED_LABELS))
def test_spam_trash_sent_and_drafts_are_skipped(label):
    assert not is_received_message({"id": "synthetic", "labelIds": ["INBOX", label]})


def test_subject_sender_and_thread_headers_do_not_restrict_scanning():
    assert is_received_message(
        {
            "id": "synthetic",
            "payload": {
                "headers": [
                    {"name": "From", "value": "friend@gmail.com"},
                    {"name": "Subject", "value": "Re: Something new"},
                    {"name": "In-Reply-To", "value": "<original>"},
                    {"name": "List-Id", "value": "example.test"},
                ]
            },
        }
    )


@pytest.mark.parametrize(
    "message",
    [
        None,
        [],
        {"labelIds": None},
        {"labelIds": "INBOX"},
        {"labelIds": [None]},
    ],
)
def test_malformed_label_data_is_skipped(message):
    assert not is_received_message(message)
