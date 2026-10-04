"""Synthetic PDF reports reconcile to the ledger and preserve local access."""

from io import BytesIO
from pathlib import Path
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pdfplumber
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor

from backend.app import create_app
from backend.pdf_report import (
    build_pdf,
    category_color,
    design_tokens,
    month_totals,
    parse_options,
    report_data,
    select_rows,
)
from .client import LocalClient
from .test_accounting import add
from .test_mobile_access import ORIGIN, pair, phone_client


def options(**changes):
    return parse_options({"start": "2026-08-01", "end": "2026-08-31", **changes})


def pdf_text(content):
    with pdfplumber.open(BytesIO(content)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def demo_row(identity, **changes):
    return {
        "id": identity,
        "date": "2026-08-01",
        "direction": "debit",
        "kind": "purchase",
        "amount_minor": 100,
        "spend_minor": 100,
        "currency": "INR",
        "counterparty": "Synthetic merchant",
        "category": "Shopping",
        "account": "Demo account",
        "allocations": [],
        "is_new": False,
        "issues": [],
        **changes,
    }


def demo_store(rows):
    return SimpleNamespace(list_transactions=lambda: rows, get_setting=lambda *_: {})


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-02-30"},
        {"start": "2026-8-01"},
        {"end": "2026-07-31"},
        {"currency": "usd"},
        {"kind": "invented"},
        {"group": "discretionary"},
        {"include_transactions": "false"},
        {"visit_ids": "a"},
        {"unexpected": True},
    ],
)
def test_invalid_dates_and_filters_rejected(changes):
    with pytest.raises(ValueError):
        options(**changes)


def test_inclusive_date_range_leap_day_and_currency(store):
    for date, amount in [
        ("2024-02-28", 100),
        ("2024-02-29", 200),
        ("2024-03-01", 300),
        ("2024-03-02", 400),
    ]:
        add(store, date=date, amount_minor=amount)
    add(store, date="2024-02-29", currency="USD", amount_minor=900)
    data = report_data(store, options(start="2024-02-29", end="2024-03-01"))
    assert set(data) == {"INR"}
    assert data["INR"]["total"] == 500
    assert [t["date"] for t in data["INR"]["rows"]] == ["2024-02-29", "2024-03-01"]


def test_total_reconciles_exclusions_movements_emi_splits_and_merge(store):
    add(store, amount_minor=1000)
    add(store, kind="financed_purchase", amount_minor=6000)
    add(store, kind="emi", category="EMIs", amount_minor=2000)
    for kind in ("card_repayment", "own_transfer", "investment", "wallet_funding"):
        add(store, kind=kind, amount_minor=7000)
    add(store, kind="income", direction="credit", amount_minor=9000)
    add(store, amount_minor=8000, excluded=True)
    add(
        store,
        amount_minor=3000,
        allocations=[
            {"type": "personal", "amount_minor": 1000, "category": "Groceries"},
            {"type": "reimbursable", "amount_minor": 2000},
        ],
    )
    original = add(store, amount_minor=500)
    duplicate = add(store, amount_minor=500)
    store.merge(duplicate["id"], original["id"])
    item = report_data(store, options())["INR"]
    assert item["total"] == 4500 == store.report("2026-08")["totals"]["spend_minor"]
    assert sum(item["categories"].values()) == item["total"]
    assert sum(item["accounts"].values()) == item["total"]
    assert sum(item["groups"].values()) == item["total"]
    assert duplicate["id"] not in {t["id"] for t in item["rows"]}


def test_refund_uses_original_split_outside_date_range_and_exact_minor_units(store):
    store.set_setting("financial_context", {"fixed_categories": ["Rent & home"]})
    purchase = add(
        store,
        date="2026-07-31",
        amount_minor=4,
        category="Other",
        allocations=[
            {"type": "personal", "amount_minor": 1, "category": "Rent & home"},
            {"type": "personal", "amount_minor": 2, "category": "Shopping"},
            {"type": "reimbursable", "amount_minor": 1},
        ],
    )
    refund = add(
        store,
        amount_minor=1,
        kind="refund",
        direction="credit",
        category="Uncategorized",
        linked_to=purchase["id"],
    )
    item = report_data(store, options())["INR"]
    assert [t["id"] for t in item["rows"]] == [refund["id"]]
    assert item["total"] == -1
    assert item["gross"] == 0 and item["refunds"] == 1
    assert item["categories"] == {"Rent & home": 0, "Shopping": -1}
    assert sum(item["groups"].values()) == -1
    text = pdf_text(build_pdf(store, options()))
    assert "INR -0.01" in text and "Refunds" in text


def test_category_and_group_filters_keep_full_personal_share_and_linked_refunds(store):
    store.set_setting("financial_context", {"fixed_categories": ["Rent & home"]})
    purchase = add(
        store,
        amount_minor=1000,
        category="Other",
        allocations=[
            {"type": "personal", "amount_minor": 300, "category": "Rent & home"},
            {"type": "personal", "amount_minor": 600, "category": "Shopping"},
            {"type": "reimbursable", "amount_minor": 100},
        ],
    )
    refund = add(
        store,
        date="2026-08-02",
        amount_minor=90,
        kind="refund",
        direction="credit",
        category="Uncategorized",
        linked_to=purchase["id"],
    )
    add(store, category="Groceries", amount_minor=100)
    for filters in ({"category": "Rent & home"}, {"group": "fixed"}):
        item = report_data(store, options(**filters))["INR"]
        assert {t["id"] for t in item["rows"]} == {purchase["id"], refund["id"]}
        assert item["total"] == 810
        assert item["categories"] == {"Rent & home": 270, "Shopping": 540}
        assert "full personal share" in pdf_text(build_pdf(store, options(**filters)))


def test_type_and_search_match_display_payee_account_reference_and_category():
    target = demo_row(
        "target",
        merchant_display="Display merchant",
        counterparty="Raw payee",
        account="Synthetic bank",
        reference="SYNTH-REFERENCE",
        category="Groceries",
    )
    rows = [target, demo_row("wrong-type", kind="fee"), demo_row("other")]
    for search in (
        " DISPLAY MERCHANT ",
        "raw PAYEE",
        "synthetic BANK",
        "synth-reference",
        "groceries",
    ):
        assert select_rows(rows, options(search=search, kind="purchase"), {}) == [target]
    assert select_rows(rows, options(search="missing"), {}) == []
    assert [r["id"] for r in select_rows(rows, options(kind="fee"), {})] == ["wrong-type"]


def test_new_view_preserves_visit_rows_and_separates_currencies():
    rows = [
        demo_row("new-inr", is_new=True),
        demo_row("seen-visit", currency="USD", spend_minor=500, amount_minor=500),
        demo_row("seen"),
        demo_row("old-new", date="2026-07-31", is_new=True),
    ]
    opts = options(new_only=True, visit_ids=["seen-visit"])
    data = report_data(demo_store(rows), opts)
    assert set(data) == {"INR", "USD"}
    assert data["INR"]["total"] == 100
    assert data["USD"]["total"] == 500
    text = pdf_text(build_pdf(demo_store(rows), opts))
    assert "All currencies (separate totals)" in text
    assert "INR 1.00" in text and "USD 5.00" in text
    assert "INR 6.00" not in text


def test_new_view_category_matches_stored_split_fields_without_month_evidence():
    purchase = demo_row("purchase", category="Shopping")
    refund = demo_row(
        "refund",
        category="Uncategorized",
        kind="refund",
        spend_minor=-20,
        amount_minor=20,
        linked_to="purchase",
        is_new=True,
    )
    # New imports use their stored categories; regular monthly exploration also
    # includes analytical refund evidence where a purchase category is available.
    rows = [purchase, refund]
    assert select_rows(rows, options(new_only=True, category="Shopping"), {}) == []
    assert select_rows(rows, options(category="Shopping"), {}) == [purchase, refund]


def test_optional_transactions_omit_description_without_losing_summary(store):
    add(store, counterparty="Synthetic detail sentinel", amount_minor=125, excluded=True)
    summary = pdf_text(build_pdf(store, options()))
    detailed = pdf_text(build_pdf(store, options(include_transactions=True)))
    assert "Synthetic detail sentinel" not in summary
    assert "Synthetic detail sentinel" in detailed
    assert "INR 1.25 debit / INR 0.00" in " ".join(detailed.split())
    assert "personal spending" in summary and "0.00" in summary
    assert "not verified payment methods" in summary


def test_empty_report_still_downloads_valid_a4_and_dates(store):
    content = build_pdf(store, options(include_transactions=True))
    assert content.startswith(b"%PDF-")
    with pdfplumber.open(BytesIO(content)) as document:
        assert len(document.pages) == 1
        page = document.pages[0]
        assert (page.width, page.height) == pytest.approx(A4, abs=0.01)
        text = page.extract_text()
        assert "No transactions match these dates and filters" in text
        assert "2026-08-01 to 2026-08-31" in text
        assert "Page 1 of 1" in text


def test_525_transactions_paginate_with_repeated_headers_and_complete_content():
    rows = [demo_row(f"row-{i:04d}", counterparty=f"Demo merchant {i:04d}") for i in range(525)]
    content = build_pdf(demo_store(rows), options(include_transactions=True))
    with pdfplumber.open(BytesIO(content)) as document:
        assert len(document.pages) > 10
        texts = []
        for number, page in enumerate(document.pages, 1):
            assert (page.width, page.height) == pytest.approx(A4, abs=0.01)
            text = page.extract_text()
            texts.append(text)
            assert "2026-08-01 to 2026-08-31" in text
            assert f"Page {number} of {len(document.pages)}" in text
            if "Demo merchant" in text:
                assert "Description" in text and "Category" in text
            # Every visible glyph stays inside the A4 page, including footer.
            assert all(0 <= char["x0"] <= char["x1"] <= page.width for char in page.chars)
            assert all(0 <= char["top"] <= char["bottom"] <= page.height for char in page.chars)
        joined = "\n".join(texts)
        for i in range(525):
            assert joined.count(f"Demo merchant {i:04d}") == 1
        assert "INR 525.00" in joined


def test_untrusted_long_and_unicode_labels_are_readable_without_markup():
    label = "Café & <demo> " + "longlabel " * 160 + " END-SENTINEL"
    rows = [demo_row("long", counterparty=label, category="Shopping 😀")]
    text = pdf_text(build_pdf(demo_store(rows), options(include_transactions=True)))
    assert "Café & <demo>" in text
    assert "END-SENTINEL" in text
    assert "[U+1F600]" in text


def test_large_amounts_and_long_category_names_fit_without_losing_values():
    maximum = 9_007_199_254_740_991
    category = "Synthetic category " * 105 + " CATEGORY-END"
    rows = [demo_row("largest", amount_minor=maximum, spend_minor=maximum, category=category)]
    content = build_pdf(demo_store(rows), options(include_transactions=True))
    with pdfplumber.open(BytesIO(content)) as document:
        text = " ".join(" ".join((page.extract_text() or "").split()) for page in document.pages)
        assert "90,071,992,547,409.91" in text
        assert "CATEGORY-END" in text
        for page in document.pages:
            assert all(0 <= char["x0"] <= char["x1"] <= page.width for char in page.chars)
            assert all(0 <= char["top"] <= char["bottom"] <= page.height for char in page.chars)


@pytest.mark.parametrize("currency", ["INR", "USD"])
@pytest.mark.parametrize("signed", [False, True])
def test_six_extreme_months_use_readable_labels_and_preserve_exact_month_values(currency, signed):
    maximum = 9_007_199_254_740_991
    rows = []
    for month in range(1, 7):
        value = maximum - month
        refund = signed and month > 3
        rows.append(
            demo_row(
                str(month),
                date=f"2026-{month:02d}-01",
                currency=currency,
                amount_minor=value,
                spend_minor=-value if refund else value,
                kind="refund" if refund else "purchase",
                direction="credit" if refund else "debit",
            )
        )
    content = build_pdf(
        demo_store(rows), options(start="2026-01-01", end="2026-06-30", currency=currency)
    )
    with pdfplumber.open(BytesIO(content)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
        assert "K/M/B/T abbreviations. Exact figures are shown below." in text
        assert "Exact net spending" in text
        for label in ("Jan 26", "Feb 26", "Mar 26", "Apr 26", "May 26", "Jun 26"):
            assert label in text
        for row in rows:
            assert row["date"][:7] in text
            value = row["spend_minor"]
            exact = (
                f"{currency} {'-' if value < 0 else ''}{abs(value) // 100:,}.{abs(value) % 100:02d}"
            )
            assert exact in text
        for page in document.pages:
            # The previous six-month chart shrank maximum-value labels to 2.93pt.
            assert min(char["size"] for char in page.chars) >= 6
            assert all(0 <= char["x0"] <= char["x1"] <= page.width for char in page.chars)
            assert all(0 <= char["top"] <= char["bottom"] <= page.height for char in page.chars)


def test_report_palette_and_category_colors_match_current_app_source():
    source = (Path(__file__).resolve().parents[1] / "frontend/styles/tokens.css").read_text()
    root = source.split(":root {", 1)[1].split(".dark {", 1)[0]
    tokens = design_tokens()
    for name in (
        "background",
        "foreground",
        "primary",
        "border",
        "secondary",
        "muted-foreground",
        "graphic-regular",
        "graphic-fixed",
        "graphic-unavoidable",
        *(f"chart-{i}" for i in range(1, 7)),
    ):
        match = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}});", root)
        assert match is not None and tokens[name] == match.group(1)
    # These indices were checked against the actual frontend categoryColor()
    # in Node, including an astral codepoint to detect UTF-16 hash drift.
    for name, index in (
        ("Food & dining", 4),
        ("Groceries", 6),
        ("Rent & home", 5),
        ("Shopping", 3),
        ("旅費😀", 3),
    ):
        assert category_color(name) == HexColor(tokens[f"chart-{index}"])


def test_monthly_chart_totals_reconcile_filtered_multi_year_currency_scopes():
    rows = [
        demo_row("inr-dec", date="2024-12-31", counterparty="Chart selection", is_new=True),
        demo_row(
            "inr-jan",
            date="2025-01-01",
            counterparty="Chart selection",
            is_new=True,
            amount_minor=50,
            spend_minor=50,
        ),
        demo_row(
            "inr-refund",
            date="2025-01-02",
            counterparty="Chart selection",
            is_new=True,
            kind="refund",
            direction="credit",
            amount_minor=20,
            spend_minor=-20,
        ),
        demo_row(
            "usd-dec",
            date="2024-12-15",
            counterparty="Chart selection",
            is_new=True,
            currency="USD",
            amount_minor=800,
            spend_minor=800,
        ),
        demo_row(
            "usd-movement",
            date="2025-02-01",
            counterparty="Chart selection",
            is_new=True,
            currency="USD",
            kind="own_transfer",
            spend_minor=0,
        ),
        demo_row("wrong-search", date="2025-01-01", is_new=True, spend_minor=999),
        demo_row("outside-dates", date="2026-01-01", counterparty="Chart selection", is_new=True),
        demo_row("already-seen", date="2025-01-01", counterparty="Chart selection"),
    ]
    selected = options(
        start="2024-12-01",
        end="2025-02-28",
        new_only=True,
        currency="EUR",
        search="Chart selection",
    )
    data = report_data(demo_store(rows), selected)
    assert set(data) == {"INR", "USD"}
    assert month_totals(data["INR"]) == [("2024-12", 100), ("2025-01", 30)]
    assert month_totals(data["USD"]) == [("2024-12", 800), ("2025-02", 0)]
    for item in data.values():
        assert sum(value for _, value in month_totals(item)) == item["total"]
    text = pdf_text(build_pdf(demo_store(rows), selected))
    assert "Same filters and selected dates" in text


def test_monthly_chart_retains_all_thirteen_months_across_panel_boundaries():
    months = [f"2024-{i:02d}" for i in range(1, 13)] + ["2025-01"]
    rows = [
        demo_row(
            f"month-{month}",
            date=month + "-15",
            amount_minor=(i + 1) * 100,
            spend_minor=(i + 1) * 100,
        )
        for i, month in enumerate(months)
    ]
    content = build_pdf(demo_store(rows), options(start="2024-01-01", end="2025-01-31"))
    text = pdf_text(content)
    for label in (
        "Jan 24",
        "Feb 24",
        "Mar 24",
        "Apr 24",
        "May 24",
        "Jun 24",
        "Jul 24",
        "Aug 24",
        "Sep 24",
        "Oct 24",
        "Nov 24",
        "Dec 24",
        "Jan 25",
    ):
        assert label in text
    assert text.count("Same filters and selected dates") == 3
    assert text.count("Recorded personal spending") == 1
    assert "INR 91.00" in text
    with pdfplumber.open(BytesIO(content)) as document:
        for page in document.pages:
            assert all(0 <= char["x0"] <= char["x1"] <= page.width for char in page.chars)
            assert all(0 <= char["top"] <= char["bottom"] <= page.height for char in page.chars)


def test_composition_ribbon_preserves_negative_group_note_and_signed_legend(store):
    store.set_setting("financial_context", {"fixed_categories": ["Rent & home"]})
    add(store, amount_minor=200, category="Shopping")
    add(store, amount_minor=50, category="Rent & home", kind="refund", direction="credit")
    item = report_data(store, options())["INR"]
    assert item["groups"] == {"other": 200, "fixed": -50}
    assert item["total"] == 150
    text = pdf_text(build_pdf(store, options()))
    assert "Positive ribbon; negative groups are net refunds." in text
    assert "-₹0.50" in text and "₹2" in text
    assert "Tracks show refunds left of zero and spending right of zero." in text


def test_desktop_pdf_requires_auth_csrf_and_same_origin_and_is_not_cached(store):
    transaction = add(store, counterparty="Local synthetic transaction")
    with store.tx() as db:
        db.execute(
            "INSERT INTO jobs(id,state,started_at) VALUES('pdf-demo','complete','2026-08-01')"
        )
        db.execute(
            "INSERT INTO sync_additions(transaction_id,job_id) VALUES(?,'pdf-demo')",
            (transaction["id"],),
        )
    app = create_app(store, start_scheduler=False)
    payload = options(new_only=True).model_dump()
    with LocalClient(app) as owner, TestClient(app) as stranger:
        csrf = owner.get("/api/session").json()["csrf"]
        headers = {"X-CSRF-Token": csrf}
        assert stranger.post("/api/report.pdf", json=payload).status_code == 401
        assert owner.post("/api/report.pdf", json=payload).status_code == 403
        assert (
            owner.post(
                "/api/report.pdf", json=payload, headers={**headers, "Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
        assert (
            owner.post(
                "/api/report.pdf", json=payload, headers={**headers, "Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            owner.post(
                "/api/report.pdf", json={**payload, "start": "invalid"}, headers=headers
            ).status_code
            == 400
        )
        before = store.list_transactions()
        response = owner.post("/api/report.pdf", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["cache-control"] == "no-store"
        assert "kharcha-2026-08-01-to-2026-08-31.pdf" in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")
        assert store.list_transactions() == before
        assert store.sync_summary("pdf-demo") == {"added": 1, "unseen": 1}


def test_paired_phone_download_requires_own_session_and_revocation_takes_effect(store):
    add(store)
    app = create_app(store, start_scheduler=False)
    payload = options(include_transactions=True).model_dump()
    with LocalClient(app) as owner, phone_client(app) as phone:
        assert (
            phone.post("/api/report.pdf", json=payload, headers={"Origin": ORIGIN}).status_code
            == 401
        )
        request, _, desktop_headers = pair(app, phone, owner)
        assert (
            phone.post(
                "/api/report.pdf", json=payload, headers={**desktop_headers, "Origin": ORIGIN}
            ).status_code
            == 403
        )
        phone_csrf = phone.get("/api/session").json()["csrf"]
        response = phone.post(
            "/api/report.pdf", json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": phone_csrf}
        )
        assert response.status_code == 200 and response.content.startswith(b"%PDF-")
        assert response.headers["cache-control"] == "no-store"
        assert (
            owner.post(
                "/api/mobile/revoke", json={"id": request["id"]}, headers=desktop_headers
            ).status_code
            == 200
        )
        assert (
            phone.post(
                "/api/report.pdf",
                json=payload,
                headers={"Origin": ORIGIN, "X-CSRF-Token": phone_csrf},
            ).status_code
            == 401
        )
