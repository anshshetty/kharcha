import base64
import pytest
from .client import LocalClient as TestClient
from backend.app import create_app
from backend.statements import parse_tables, preview, commit

TABLE = [
    ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Balance"],
    ["01/08/2026", "SHOP", "1,250.50", "", "8,749.50"],
    ["02/08/2026", "SALARY", "", "50000.00", "58,749.50"],
]


def test_columns_do_not_import_balance():
    rows, warnings = parse_tables([TABLE], "2026-08", "Bank 1234", "INR")
    assert [r["amount_minor"] for r in rows] == [125050, 5000000]
    assert [r["direction"] for r in rows] == ["debit", "credit"]
    assert not warnings
    assert not parse_tables([TABLE], "2026-07", "Bank", "INR")[0]
    bad = TABLE[:1] + [["03/08/2026", "AMBIGUOUS", "50", "40", "100"]]
    assert parse_tables([bad], "2026-08", "Bank", "INR")[1]


def mock_pdf(monkeypatch):
    import pdfplumber

    class Page:
        page_number = 1

        def extract_tables(self, *args):
            return [TABLE]

    class PDF:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(pdfplumber, "open", lambda *args, **kwargs: PDF())


def upload():
    return dict(
        file=base64.b64encode(b"%PDF-test").decode(),
        month="2026-08",
        account="Bank 1234",
        password="secret",
    )


def test_review_import_idempotency_and_matching(store, monkeypatch):
    mock_pdf(monkeypatch)
    rows, _ = parse_tables([TABLE], "2026-08", "Bank 1234", "INR")
    store.create_manual(rows[0])
    p = preview(store, upload())
    assert p["rows"][0]["possible_matches"]
    assert len(store.list_transactions()) == 1
    assert (
        commit(store, {"token": p["token"], "rows": [{"index": 1, "kind": "income"}]})["imported"]
        == 1
    )
    p = preview(store, upload())
    assert p["rows"][1]["already_imported"]
    assert commit(store, {"token": p["token"], "rows": [{"index": 1}]})["skipped"] == 1
    assert len(store.list_transactions()) == 2


def test_invalid_selection_is_atomic(store, monkeypatch):
    mock_pdf(monkeypatch)
    p = preview(store, upload())
    with pytest.raises(ValueError):
        commit(
            store, {"token": p["token"], "rows": [{"index": 0}, {"index": 1, "kind": "purchase"}]}
        )
    assert not store.list_transactions()


def test_api_invalid_pdf(store):
    with TestClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        data = {**upload(), "file": base64.b64encode(b"not pdf").decode()}
        assert client.post("/api/statements/preview", json=data, headers=headers).status_code == 400
        assert client.post("/api/statements/preview", json=data).status_code == 403


def statement_pdf(date_header="Date"):
    """Small real, ruled-table PDF fixture, built entirely in memory."""
    commands = ["0.5 w"]
    for x in [30, 115, 280, 365, 450, 560]:
        commands.append(f"{x} 620 m {x} 710 l S")
    for y in [620, 650, 680, 710]:
        commands.append(f"30 {y} m 560 {y} l S")
    values = [
        [date_header, "Description", "Debit", "Credit", "Balance"],
        ["01/08/2026", "SHOP", "1250.50", "", "8749.50"],
        ["02/08/2026", "SALARY", "", "50000.00", "58749.50"],
    ]
    for cells, y in zip(values, [690, 660, 630]):
        for value, x in zip(cells, [34, 119, 284, 369, 454]):
            commands.append(f"BT /F1 10 Tf {x} {y} Td ({value}) Tj ET")
    stream = "\n".join(commands).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode()


def test_real_pdf_api_flow(store):
    with TestClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        data = {**upload(), "password": "", "file": base64.b64encode(statement_pdf()).decode()}
        response = client.post("/api/statements/preview", json=data, headers=headers)
        assert response.status_code == 200, response.text
        p = response.json()
        assert [r["amount_minor"] for r in p["rows"]] == [125050, 5000000]
        assert not p["warnings"]
        assert not store.list_transactions()
        response = client.post(
            "/api/statements/import",
            headers=headers,
            json={
                "token": p["token"],
                "rows": [{"index": 0, "kind": "own_transfer"}, {"index": 1, "kind": "income"}],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["imported"] == 2
        assert {r["kind"] for r in store.list_transactions()} == {"own_transfer", "income"}


def test_axis_header_and_wrong_month(store):
    data = {
        **upload(),
        "password": "",
        "file": base64.b64encode(statement_pdf("Tran Date")).decode(),
    }
    result = preview(store, data)
    assert len(result["rows"]) == 2
    assert result["rows"][0]["direction"] == "debit"
    with pytest.raises(ValueError, match="August 2026"):
        preview(store, {**data, "month": "2026-09"})


def test_commit_skips_existing_email_and_stale_preview(store, monkeypatch):
    mock_pdf(monkeypatch)
    p = preview(store, upload())
    rows, _ = parse_tables([TABLE], "2026-08", "Bank 1234", "INR")
    # An email/manual transaction can arrive after the preview was shown.
    original = store.create_manual(rows[0])
    result = commit(store, {"token": p["token"], "rows": [{"index": 0}, {"index": 1}]})
    assert result["imported"] == 1 and result["skipped"] == 1
    assert len(store.list_transactions()) == 2
    assert store.transaction(original["id"])["counterparty"] == "SHOP"


def test_reference_matches_posting_date_difference(store):
    from backend.statements import candidates

    row = dict(
        date="2026-09-02",
        direction="debit",
        amount_minor=10000,
        currency="INR",
        counterparty="UPI/P2M/123456789012/SHOP",
    )
    existing = [{**row, "id": "email", "date": "2026-09-01", "reference": "123456789012"}]
    assert candidates(row, existing) == ["email"]
    assert candidates({**row, "direction": "credit"}, existing) == []
    assert candidates({**row, "amount_minor": 20000}, existing) == []


def test_statement_enriches_exact_match_and_preserves_corrections(store):
    from backend.statements import enrich, enrichment_target

    row = dict(
        date="2026-08-02",
        direction="debit",
        amount_minor=10000,
        currency="INR",
        counterparty="UPI/P2M/123456789012/NETFLIX/HDFC",
        reference="123456789012",
        reference_namespace="upi",
        account="Bank",
        kind="purchase",
    )
    target = store.create_manual(
        {**row, "counterparty": "Unknown recipient", "date_basis": "received", "date": "2026-08-01"}
    )
    with store.tx() as db:
        store.issue(
            db, "evidence", "Transaction date missing; email received date used", target["id"]
        )
        store.issue(db, "import_audit", "Review transaction type", target["id"])
        assert enrichment_target(row, [target])["id"] == target["id"]
        assert enrich(store, db, row, target, "test.pdf") == (1, 1)
    changed = store.transaction(target["id"])
    assert changed["counterparty"] == "NETFLIX"
    assert changed["category"] == "Subscriptions"
    assert changed["date"] == row["date"]
    assert len(changed["issues"]) == 1 and changed["issues"][0]["kind"] == "import_audit"
    store.update(target["id"], {"counterparty": "My correction", "category": "Shopping"})
    with store.tx() as db:
        enrich(store, db, row, store.transaction(target["id"]), "second.pdf")
    assert store.transaction(target["id"])["counterparty"] == "My correction"
    assert store.transaction(target["id"])["category"] == "Shopping"
    assert enrichment_target({**row, "reference": "different"}, [target]) is None
    assert enrichment_target(row, [target, {**target, "id": "another"}]) is None
