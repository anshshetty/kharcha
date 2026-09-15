import pytest
from backend.insights import brief, review_items, validate_context
from .test_accounting import add


def test_project_split_keeps_ledger_total_and_exempts(store):
    store.set_setting("financial_context", {"project_categories": ["Home construction"]})
    with store.tx() as db:
        db.execute("INSERT INTO categories VALUES('Home construction')")
    add(store, category="Home construction", amount_minor=100000)
    add(store, category="Shopping", amount_minor=20000)
    add(store, category="Own transfers", amount_minor=90000000, excluded=True)
    b = brief(store, "2026-08", "INR")
    assert b["spending_minor"] == 120000
    assert b["project_spending_minor"] == 100000
    assert b["everyday_spending_minor"] == 20000
    assert b["excluded_count"] == 1
    store.set_setting(
        "financial_context",
        validate_context(
            {"monthly_target_minor": 30000, "project_categories": ["Home construction"]}
        ),
    )
    assert brief(store, "2026-08", "INR")["remaining_minor"] == 10000
    assert brief(store, "2026-08", "USD")["target_minor"] is None


def test_conflict_review_uses_effective_corrections(store):
    add(store, category="Own transfers", amount_minor=50000)
    t = store.list_transactions()[0]
    items = review_items(store)
    assert items[0]["derived"] and items[0]["amount_minor"] == 50000
    store.update(t["id"], {"excluded": True})
    assert not [i for i in review_items(store) if i.get("derived")]


def test_context_rejects_invalid_target():
    for value in [True, -1, 1.5, "100"]:
        with pytest.raises(ValueError):
            validate_context({"monthly_target_minor": value})


def test_context_api_roundtrip(store):
    from .client import LocalClient as TestClient
    from backend.app import create_app

    with TestClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        data = {
            "priorities": "Keep travel money separate",
            "project_categories": ["Travel"],
            "monthly_target_minor": 500000,
        }
        assert client.put("/api/financial-context", json=data).status_code == 403
        assert client.put("/api/financial-context", json=data, headers=headers).status_code == 200
        assert client.get("/api/financial-context").json()["priorities"] == data["priorities"]
        assert client.get("/api/financial-brief?month=2026-08").json()["context"][
            "project_categories"
        ] == ["Travel"]
