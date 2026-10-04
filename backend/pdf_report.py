"""Local, in-memory A4 reports using the ledger's existing accounting rules."""

from collections import defaultdict
from copy import deepcopy
from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from .models import Currency, Kind, ShortText
from .spending_focus import expense_parts
from .store import now


class ReportOptions(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    start: str
    end: str
    currency: Currency = "INR"
    category: ShortText = "all"
    kind: Kind | Literal["all"] = "all"
    search: ShortText = ""
    group: Literal["", "regular", "fixed", "unavoidable"] = ""
    new_only: bool = False
    visit_ids: Annotated[list[ShortText], Field(max_length=100000)] = Field(default_factory=list)
    include_transactions: bool = False

    @field_validator("start", "end")
    @classmethod
    def valid_date(cls, value):
        if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use YYYY-MM-DD")
        return value


def parse_options(body):
    try:
        options = ReportOptions.model_validate(body)
    except ValidationError:
        raise ValueError("Choose valid report dates and filters") from None
    if options.start > options.end:
        raise ValueError("Report start date must be on or before the end date")
    return options


def select_rows(rows, options, policy):
    by_id = {t["id"]: t for t in rows}
    query = options.search.strip().lower()
    evidence_months = (
        {
            (t["currency"], t["date"][:7])
            for t in rows
            if any(cat == options.category for cat, _, _ in expense_parts(t, by_id, policy))
        }
        if options.category != "all" and not options.new_only
        else set()
    )
    selected = []
    visit_ids = set(options.visit_ids)
    for t in rows:
        # New view spans currencies; never convert or combine their money totals.
        if not options.start <= t["date"] <= options.end:
            continue
        if options.new_only:
            if not t.get("is_new") and t["id"] not in visit_ids:
                continue
        elif t["currency"] != options.currency:
            continue
        parts = expense_parts(t, by_id, policy)
        if options.group and not any(
            ("regular" if group == "other" else group) == options.group for _, group, _ in parts
        ):
            continue
        if options.category != "all":
            # Monthly explorer uses analytical category evidence where available,
            # otherwise the row category or any split category, including zero-spend rows.
            analytical = any(cat == options.category for cat, _, _ in parts)
            month_has_evidence = (t["currency"], t["date"][:7]) in evidence_months
            match = (
                analytical
                if month_has_evidence
                else (
                    t["category"] == options.category
                    or any(a.get("category") == options.category for a in t.get("allocations", []))
                )
            )
            if not match:
                continue
        if options.kind != "all" and t["kind"] != options.kind:
            continue
        text = " ".join(
            str(t.get(key) or "")
            for key in (
                "merchant_display",
                "counterparty",
                "account",
                "reference",
                "category",
            )
        ).lower()
        if query and query not in text:
            continue
        selected.append(t)
    return sorted(selected, key=lambda t: (t["date"], t["id"]))


def report_data(store, options):
    rows = store.list_transactions()
    policy = store.get_setting("financial_context", {})
    selected = select_rows(rows, options, policy)
    by_id = {t["id"]: t for t in rows}
    currencies = {}
    for t in selected:
        item = currencies.setdefault(
            t["currency"],
            {
                "rows": [],
                "total": 0,
                "gross": 0,
                "refunds": 0,
                "categories": defaultdict(int),
                "accounts": defaultdict(int),
                "groups": defaultdict(int),
                "review": 0,
            },
        )
        item["rows"].append(t)
        amount = t["spend_minor"]
        item["total"] += amount
        item["gross"] += max(0, amount)
        item["refunds"] += max(0, -amount)
        item["review"] += bool(t.get("issues"))
        if amount:
            item["accounts"][t.get("account") or "Unknown account"] += amount
        for category, group, value in expense_parts(t, by_id, policy):
            item["categories"][category] += value
            item["groups"][group] += value
    if not currencies:
        currencies[options.currency] = {
            "rows": [],
            "total": 0,
            "gross": 0,
            "refunds": 0,
            "categories": {},
            "accounts": {},
            "groups": {},
            "review": 0,
        }
    return currencies


@lru_cache(maxsize=1)
def report_font():
    # Reuse the app's licensed, bundled font. No network or OS font dependency.
    path = Path(__file__).resolve().parent / "assets/manrope-report.ttf"
    font = TTFont("KharchaReport", str(path))
    pdfmetrics.registerFont(font)
    return font


def readable(value):
    coverage = report_font().face.charToGlyph
    text = " ".join(str(value).split())
    # Unsupported glyphs stay explicit instead of silently vanishing or becoming boxes.
    return "".join(c if ord(c) in coverage else f"[U+{ord(c):04X}]" for c in text)


def money(value, currency):
    sign = "-" if value < 0 else ""
    return f"{currency} {sign}{abs(value) // 100:,}.{abs(value) % 100:02d}"


class SpendingBar(Flowable):
    def __init__(self, amount, maximum, width):
        super().__init__()
        self.width, self.height = width, 14
        self.amount, self.maximum = amount, max(1, maximum)

    def draw(self):
        c = self.canv
        center = self.width / 2
        c.setStrokeColor(colors.HexColor("#bccac1"))
        c.line(center, 1, center, 13)
        size = abs(self.amount) / self.maximum * (center - 2)
        c.setFillColor(colors.HexColor("#b35b38" if self.amount < 0 else "#245d46"))
        c.rect(center - size if self.amount < 0 else center, 3, size, 8, fill=1, stroke=0)


def build_pdf(store, options):
    report_font()
    data = report_data(store, options)
    stream = BytesIO()
    width, height = A4
    margin = 42
    body_width = width - 2 * margin
    ink = colors.HexColor("#233b31")
    style = ParagraphStyle(
        "report", fontName="KharchaReport", fontSize=9, leading=13, textColor=ink
    )
    small = ParagraphStyle("small", parent=style, fontSize=8, leading=11)
    title = ParagraphStyle("title", parent=style, fontSize=25, leading=31, spaceAfter=10)
    heading = ParagraphStyle(
        "heading", parent=style, fontSize=13, leading=18, spaceBefore=15, spaceAfter=8
    )
    right = ParagraphStyle("right", parent=small, alignment=TA_RIGHT)

    def p(text, use_style=style):
        return Paragraph(escape(readable(text)), use_style)

    def table(headers, entries, widths):
        content = [[p(h, small) for h in headers]]
        content.extend(entries)
        result = LongTable(content, colWidths=widths, repeatRows=1, splitInRow=1, hAlign="LEFT")
        result.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f0eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#bccac1")),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#dce5de")),
                ]
            )
        )
        return result

    filters = ["All currencies (separate totals)" if options.new_only else options.currency]
    for label, value in (
        ("Category", options.category),
        ("Type", options.kind),
        ("Search", options.search),
        ("Spending group", options.group),
    ):
        if value and value != "all":
            filters.append(f"{label}: {value}")
    if options.new_only:
        filters.append("New transactions, including those viewed during this visit")
    story = [
        p("Kharcha", title),
        p("Spending report", heading),
        p(f"{options.start} to {options.end} (inclusive)"),
        p("Applied filters: " + "; ".join(filters), small),
        p(
            "Generated "
            + now()[:19].replace("T", " ")
            + " IST. Based on recorded ledger data; coverage may be incomplete.",
            small,
        ),
        Spacer(1, 10),
        p(
            "Total personal spending includes fixed and unavoidable costs, personal split shares and refunds received in this period. Income, transfers, card repayments and excluded payments contribute zero.",
            small,
        ),
    ]
    if options.category != "all" or options.group:
        story.append(
            p(
                "Category and spending-group filters select matching payments. Totals include each matching payment's full personal share, including other split categories.",
                small,
            )
        )
    story.append(
        p(
            "Payment accounts are the recorded accounts, not verified payment methods. Unsupported text characters appear as [U+code] so no label is silently omitted.",
            small,
        )
    )
    if store.get_setting("demo", {}).get("as_of"):
        story.insert(0, p("SYNTHETIC DEMO - Fictional data, no real payments", heading))
    for currency, item in sorted(data.items()):
        story.extend(
            [
                p(currency + " personal spending", heading),
                p(money(item["total"], currency), title),
                p(
                    f"{len(item['rows'])} matching transactions | Gross spending: {money(item['gross'], currency)} | Refunds: {money(item['refunds'], currency)}",
                    small,
                ),
                p(
                    f"Regular: {money(item['groups'].get('other', 0), currency)} | Fixed: {money(item['groups'].get('fixed', 0), currency)} | Unavoidable: {money(item['groups'].get('unavoidable', 0), currency)}",
                    small,
                ),
            ]
        )
        if item["review"]:
            story.append(
                p(
                    f"Provisional: {item['review']} matching transactions need evidence review.",
                    small,
                )
            )
        if not item["rows"]:
            story.append(
                p(
                    "No transactions match these dates and filters. No recorded spending is not proof of complete coverage.",
                    heading,
                )
            )
        for name, groups in (
            ("Category breakdown", item["categories"]),
            ("Payment accounts", item["accounts"]),
        ):
            story.append(p(name, heading))
            entries = sorted(groups.items(), key=lambda entry: (-entry[1], entry[0]))
            if not entries:
                story.append(p("No personal spending in this selection.", small))
                continue
            maximum = max(abs(amount) for _, amount in entries)
            story.append(
                table(
                    [
                        "Category" if name.startswith("Category") else "Account",
                        "Net spending",
                        "Refunds ← 0 → spending",
                    ],
                    [
                        [
                            p(label, small),
                            p(money(amount, currency), right),
                            SpendingBar(amount, maximum, 133),
                        ]
                        for label, amount in entries
                    ],
                    [body_width - 269, 120, 149],
                )
            )
        if options.include_transactions and item["rows"]:
            story.extend(
                [
                    p("Transactions", heading),
                    p(
                        "Amount is the original debit/credit. Personal spending is the contribution to the total above; split and excluded payments can differ. Refund spending is negative.",
                        small,
                    ),
                ]
            )
            story.append(
                table(
                    ["Date", "Description", "Category", "Amount / personal spend"],
                    [
                        [
                            p(t["date"], small),
                            p(
                                (t.get("merchant_display") or t["counterparty"])
                                + " · "
                                + t["kind"].replace("_", " "),
                                small,
                            ),
                            p(t["category"], small),
                            p(
                                money(t["amount_minor"], currency)
                                + " "
                                + t["direction"]
                                + " / "
                                + money(t["spend_minor"], currency),
                                right,
                            ),
                        ]
                        for t in item["rows"]
                    ],
                    [68, body_width - 254, 80, 106],
                )
            )
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=55,
        bottomMargin=50,
        title="Kharcha spending report",
        author="Kharcha",
    )
    # Build twice to show true page totals without retaining every canvas in memory.
    page_count = 0

    def count_page(canvas, document):
        nonlocal page_count
        page_count = max(page_count, document.page)

    doc.build(deepcopy(story), onFirstPage=count_page, onLaterPages=count_page)

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("KharchaReport", 8)
        canvas.setFillColor(ink)
        canvas.drawString(margin, height - 30, "KHARCHA  /  SPENDING REPORT")
        canvas.drawString(margin, 27, f"{options.start} to {options.end}")
        canvas.drawRightString(width - margin, 27, f"Page {document.page} of {page_count}")
        canvas.restoreState()

    stream.seek(0)
    stream.truncate()
    final_doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=55,
        bottomMargin=50,
        title="Kharcha spending report",
        author="Kharcha",
    )
    final_doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
