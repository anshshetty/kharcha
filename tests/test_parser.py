import base64
import pytest
from backend.parser import decode_message, minor, parse_email, plaintext
from .conftest import email


def parse(body, subject="Transaction alert"):
    return parse_email(email(body=body, subject=subject))


@pytest.mark.parametrize(
    "value,expected", [("1,23,456.78", 12345678), ("0.01", 1), ("1,000", 100000)]
)
def test_indian_amounts(value, expected):
    assert minor(value) == expected


@pytest.mark.parametrize("value", ["1.123", "nan", "Infinity", "oops"])
def test_invalid_amounts(value):
    with pytest.raises(ValueError):
        minor(value)


@pytest.mark.parametrize(
    "subject",
    ["Your payment failed", "Your OTP for purchase", "Payment request", "Your statement is ready"],
)
def test_non_payments(subject):
    assert parse("INR 500 is the amount.", subject)["status"] == "excluded"


def test_html_alternative_not_double_counted():
    text = "INR 500 debited from account XX1234 at SHOP on 31/08/2026. UPI Ref: 123456789012"

    def enc(s):
        return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

    message = {
        "id": "a",
        "internalDate": "1788196500000",
        "payload": {
            "headers": [{"name": "From", "value": "Bank <alert@example.test>"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": enc(text)}},
                {"mimeType": "text/html", "body": {"data": enc("<div>" + text + "</div>")}},
            ],
        },
    }
    e = decode_message(message)
    assert e["body"].count("INR 500") == 1
    assert len(parse_email(e)["observations"]) == 1


def test_html_only_and_scripts():
    assert "bad code" not in plaintext("<script>bad code</script><p>INR 500 paid</p>")
    enc = base64.urlsafe_b64encode(b"<p>INR 500 debited at SHOP on 01/08/2026</p>").decode()
    e = decode_message(
        {
            "id": "a",
            "internalDate": "1785582000000",
            "payload": {"mimeType": "text/html", "body": {"data": enc}},
        }
    )
    assert parse_email(e)["status"] == "parsed"


def test_email_date_fallback_ist_boundary():
    e = email(body="INR 500 debited from account XX1234 at SHOP.")
    e["received_at"] = "2026-08-31T20:00:00+00:00"
    o = parse_email(e)["observations"][0]
    assert o["date"] == "2026-09-01" and o["date_basis"] == "received"


def test_multiple_amounts_need_review():
    assert parse("INR 1000 debited. Total INR 2000 paid.")["status"] == "review"


def test_balance_is_not_transaction_amount():
    o = parse("INR 500 debited from account XX1234 at SHOP. Available balance INR 20,000.")[
        "observations"
    ][0]
    assert o["amount_minor"] == 50000


def test_card_emi_refund_meanings():
    for body, kind in [
        ("Payment received of INR 1000 towards credit card ending 1234.", "card_repayment"),
        ("EMI of INR 10000 debited from card XX1234.", "emi"),
        ("Refund of INR 200 credited to account XX1234.", "refund"),
    ]:
        assert parse(body)["observations"][0]["kind"] == kind


def test_multi_transaction_email():
    body = "INR 500 debited at SHOP. UPI Ref: 123456789012\nINR 700 debited at OTHER. UPI Ref: 123456789013"
    assert len(parse(body)["observations"]) == 2


def test_multiple_amounts_on_one_line_do_not_recurse():
    assert parse("INR 500 debited. UPI Ref: 123456789012. INR 30 fee added.")["status"] == "review"


def test_footer_otp_warning_does_not_hide_real_payment():
    assert (
        parse("INR 500 spent at SHOP on 01/08/2026. Do not share your OTP with anyone.")["status"]
        == "parsed"
    )


def test_credit_card_marketing_is_not_completed_payment():
    assert (
        parse(
            "Get a credit card and save INR 500 on your next order.",
            "A special offer for your card",
        )["status"]
        != "parsed"
    )
