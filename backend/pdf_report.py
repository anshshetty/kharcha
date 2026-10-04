"""Local, in-memory A4 reports using the ledger's existing accounting rules."""

from collections import defaultdict
from copy import deepcopy
from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
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
    pdfmetrics.registerFont(
        TTFont("KharchaReportBold", str(path.with_name("manrope-report-bold.ttf")))
    )
    return font


def readable(value):
    coverage = report_font().face.charToGlyph
    text = " ".join(str(value).split())
    # Unsupported glyphs stay explicit instead of silently vanishing or becoming boxes.
    return "".join(c if ord(c) in coverage else f"[U+{ord(c):04X}]" for c in text)


def money(value, currency):
    sign = "-" if value < 0 else ""
    return f"{currency} {sign}{abs(value) // 100:,}.{abs(value) % 100:02d}"


@lru_cache(maxsize=1)
def design_tokens():
    """Use the same light-theme palette as the approved app, including charts."""
    source = Path(__file__).resolve().parent.parent / "frontend/styles/tokens.css"
    light = source.read_text().split(".dark {")[0]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6});", light))


def color(name):
    return colors.HexColor(design_tokens()[name])


def category_color(name):
    hashed = 0
    for character in name:
        hashed = (hashed * 31 + ord(character)) & 0xFFFFFFFF
    return color(f"chart-{hashed % 6 + 1}")


def month_totals(item):
    totals = defaultdict(int)
    for row in item["rows"]:
        totals[row["date"][:7]] += row["spend_minor"]
    return sorted(totals.items())


def display_money(value, currency):
    # Match the app's visible INR metric while keeping exact ISO values in tables.
    if currency != "INR":
        return money(value, currency)
    digits = str(abs(value) // 100)
    grouped = digits[-3:]
    digits = digits[:-3]
    while digits:
        grouped = digits[-2:] + "," + grouped
        digits = digits[:-2]
    fraction = f".{abs(value) % 100:02d}" if value % 100 else ""
    return ("-" if value < 0 else "") + "₹" + grouped + fraction


def chart_money(value, currency, width, size=6.5):
    text = display_money(value, currency)
    if pdfmetrics.stringWidth(readable(text), "KharchaReport", size) <= width:
        return text
    amount = abs(value) / 100
    unit = ""
    for unit, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if amount >= divisor:
            prefix = "₹" if currency == "INR" else currency + " "
            for precision in (2, 1, 0):
                label = (
                    ("-" if value < 0 else "") + prefix + f"{amount / divisor:.{precision}f}" + unit
                )
                if pdfmetrics.stringWidth(readable(label), "KharchaReport", size) <= width:
                    return label
            # Currency is stated on the opening and in the exact figures below.
            return ("-" if value < 0 else "") + f"{amount / divisor:.0f}" + unit
    return money(value, currency)


def draw_monthly_picture(c, months, currency, x, width):
    fitted_text(c, "The monthly picture", x, 187, width, size=9, bold=True)
    fitted_text(
        c, "Same filters and selected dates", x, 171, width, size=7, tone="muted-foreground"
    )
    if not months:
        fitted_text(c, "No recorded months", x, 87, width, size=8, tone="muted-foreground")
        return
    maximum = max(abs(value) for _, value in months) or 1
    negative = any(value < 0 for _, value in months)
    baseline = 88 if negative else 44
    bar_max = 38 if negative else 80
    col = width / len(months)
    c.setStrokeColor(color("border"))
    c.setLineWidth(0.5)
    c.line(x, baseline, x + width, baseline)
    for i, (month, value) in enumerate(months):
        start = x + i * col
        bar_w = min(42, col - 8)
        bar_x = start + (col - bar_w) / 2
        bar_h = max(2, abs(value) / maximum * bar_max) if value else 0
        tone = (
            "graphic-fixed"
            if value < 0
            else "graphic-regular"
            if i == len(months) - 1
            else "chart-bar"
        )
        c.setFillColor(color(tone))
        if bar_h:
            c.roundRect(
                bar_x,
                baseline - bar_h if value < 0 else baseline,
                bar_w,
                bar_h,
                min(3, bar_h / 2),
                fill=1,
                stroke=0,
            )
        fitted_text(
            c,
            chart_money(value, currency, col - 2),
            start + 1,
            140,
            col - 2,
            size=6.5,
            tone="muted-foreground",
        )
        label = date.fromisoformat(month + "-01").strftime("%b %y")
        fitted_text(c, label, start + 2, 29, col - 4, size=7, bold=True)
    fitted_text(
        c, "Recorded months in this selection", x, 17, width, size=7, tone="muted-foreground"
    )


class MonthlyContinuation(Flowable):
    def __init__(self, currency, months, width):
        super().__init__()
        self.width, self.height = width, 204
        self.currency, self.months = currency, months

    def draw(self):
        draw_monthly_picture(self.canv, self.months, self.currency, 0, self.width)


def fitted_text(canvas, text, x, y, width, *, size=10, bold=False, tone="foreground"):
    font = "KharchaReportBold" if bold else "KharchaReport"
    text = readable(text)
    measured = pdfmetrics.stringWidth(text, font, size)
    size = min(size, size * width / max(measured, 1))
    canvas.setFont(font, size)
    canvas.setFillColor(color(tone))
    canvas.drawString(x, y, text)


class CategoryBadge(Flowable):
    """The app's small, softly colored category symbol badge, drawn as vectors."""

    def __init__(self, name):
        super().__init__()
        self.width = self.height = 25
        self.name = name

    def draw(self):
        c = self.canv
        shade = category_color(self.name)
        pale = colors.Color(
            *(channel * 0.12 + 0.88 for channel in (shade.red, shade.green, shade.blue))
        )
        c.setFillColor(pale)
        c.roundRect(0, 0, 25, 25, 5, fill=1, stroke=0)
        c.setStrokeColor(shade)
        c.setLineWidth(1)
        name = self.name.lower()
        if "grocer" in name:
            c.rect(6, 7, 13, 7, fill=0, stroke=1)
            c.line(6, 14, 10, 19)
            c.line(19, 14, 15, 19)
            for x in (10, 15):
                c.line(x, 8, x, 13)
        elif "food" in name or "dining" in name:
            c.line(8, 6, 8, 19)
            c.line(5, 19, 5, 14)
            c.line(11, 19, 11, 14)
            c.line(5, 14, 11, 14)
            c.line(17, 6, 17, 19)
            c.line(17, 19, 19, 14)
        elif "shopping" in name:
            c.rect(6, 6, 13, 11, fill=0, stroke=1)
            c.arc(9, 14, 16, 21, 0, 180)
        elif "rent" in name or "home" in name:
            c.line(5, 13, 12, 20)
            c.line(12, 20, 20, 13)
            c.rect(7, 6, 11, 9, fill=0, stroke=1)
            c.rect(11, 6, 3, 5, fill=0, stroke=1)
        elif "transport" in name or "travel" in name:
            c.roundRect(7, 8, 11, 12, 3, fill=0, stroke=1)
            c.line(8, 15, 17, 15)
            c.circle(10, 10, 0.8, fill=0)
            c.circle(15, 10, 0.8, fill=0)
            c.line(10, 8, 7, 5)
            c.line(15, 8, 18, 5)
        else:
            for x, y in ((6, 6), (14, 6), (6, 14), (14, 14)):
                c.roundRect(x, y, 5, 5, 1, fill=0, stroke=1)


class SpendingBar(Flowable):
    def __init__(self, amount, maximum, width, shade=None, signed=False):
        super().__init__()
        self.width, self.height = width, 9
        self.amount, self.maximum = amount, max(1, maximum)
        self.shade = shade or color("primary")
        self.signed = signed

    def draw(self):
        c = self.canv
        c.setFillColor(color("secondary"))
        c.roundRect(0, 2, self.width, 4, 2, fill=1, stroke=0)
        baseline = self.width / 2 if self.signed else 0
        size = abs(self.amount) / self.maximum * (self.width / 2 if self.signed else self.width)
        c.setFillColor(color("graphic-fixed") if self.amount < 0 else self.shade)
        if size:
            c.roundRect(
                baseline - size if self.amount < 0 else baseline,
                2,
                size,
                4,
                min(2, size / 2),
                fill=1,
                stroke=0,
            )
        if self.signed:
            c.setStrokeColor(color("input-border"))
            c.line(baseline, 0, baseline, 8)


class DashboardOpening(Flowable):
    """A4 adaptation of report-opening, composition ribbon and monthly-trend."""

    def __init__(self, currency, item, width, months=None):
        super().__init__()
        self.width, self.height = width, 204
        self.currency, self.item = currency, item
        self.months = month_totals(item)[:6] if months is None else months

    def draw(self):
        c = self.canv
        left = self.width * 0.53
        right_x = left + 24
        right_width = self.width - right_x
        fitted_text(c, "Recorded personal spending", 0, 187, left, size=9, tone="muted-foreground")
        fitted_text(
            c, display_money(self.item["total"], self.currency), 0, 148, left, size=32, bold=True
        )
        fitted_text(
            c,
            self.currency + " · selected dates · after refunds",
            0,
            129,
            left,
            size=8,
            tone="muted-foreground",
        )
        values = [self.item["groups"].get(k, 0) for k in ("other", "fixed", "unavoidable")]
        positive = sum(max(0, n) for n in values)
        c.setFillColor(color("secondary"))
        c.roundRect(0, 74, left, 36, 6, fill=1, stroke=0)
        if positive:
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(0, 74, left, 36, 6)
            c.clipPath(clip, stroke=0)
            x = 0
            for n, tone in zip(values, ("graphic-regular", "graphic-fixed", "graphic-unavoidable")):
                if n <= 0:
                    continue
                w = left * n / positive
                c.setFillColor(color(tone))
                c.rect(x, 74, w, 36, fill=1, stroke=0)
                if n / positive >= 0.1:
                    c.setFillColor(colors.white)
                    c.setFont("KharchaReportBold", 9)
                    c.drawString(x + 8, 88, f"{round(n / positive * 100)}%")
                c.setStrokeColor(colors.white)
                c.setLineWidth(2)
                c.line(x + w, 74, x + w, 110)
                x += w
            c.restoreState()
        else:
            fitted_text(
                c,
                "No positive spending recorded",
                9,
                88,
                left - 18,
                size=8,
                tone="muted-foreground",
            )
        col = left / 3
        for i, (label, n, tone) in enumerate(
            zip(
                ("Regular", "Fixed", "Unavoidable"),
                values,
                ("graphic-regular", "graphic-fixed", "graphic-unavoidable"),
            )
        ):
            x = i * col
            c.setFillColor(color(tone))
            c.roundRect(x, 56, 4, 4, 1, fill=1, stroke=0)
            fitted_text(c, label, x + 7, 55, col - 9, size=7, tone="muted-foreground")
            fitted_text(
                c,
                chart_money(n, self.currency, col - 8, size=10),
                x,
                38,
                col - 8,
                size=10,
                bold=True,
            )
        note = (
            "Positive ribbon; negative groups are net refunds."
            if any(n < 0 for n in values)
            else "Recorded payments only; missing costs may affect totals."
        )
        fitted_text(c, note, 0, 17, left, size=7, tone="muted-foreground")
        draw_monthly_picture(c, self.months, self.currency, right_x, right_width)
        c.setStrokeColor(color("border"))
        c.line(0, 0, self.width, 0)


class MetricStrip(Flowable):
    def __init__(self, currency, item, width):
        super().__init__()
        self.width, self.height = width, 62
        self.currency, self.item = currency, item

    def draw(self):
        c = self.canv
        gap = 10
        w = (self.width - gap * 2) / 3
        for i, (label, value) in enumerate(
            (
                ("Recorded transactions", str(len(self.item["rows"]))),
                ("Gross spending", money(self.item["gross"], self.currency)),
                ("Money returned / Refunds", money(self.item["refunds"], self.currency)),
            )
        ):
            x = i * (w + gap)
            c.setFillColor(colors.white)
            c.setStrokeColor(color("border"))
            c.setLineWidth(0.6)
            c.roundRect(x, 7, w, 47, 7, fill=1, stroke=1)
            fitted_text(c, label, x + 10, 37, w - 20, size=7, tone="muted-foreground")
            fitted_text(c, value, x + 10, 18, w - 20, size=11, bold=True)


def build_pdf(store, options):
    report_font()
    data = report_data(store, options)
    stream = BytesIO()
    width, height = A4
    margin = 42
    body_width = width - 2 * margin
    ink = color("foreground")
    style = ParagraphStyle(
        "report", fontName="KharchaReport", fontSize=9, leading=13, textColor=ink
    )
    small = ParagraphStyle(
        "small", parent=style, fontSize=8, leading=11, textColor=color("muted-foreground")
    )
    title = ParagraphStyle(
        "title", parent=style, fontName="KharchaReportBold", fontSize=20, leading=27, spaceAfter=8
    )
    heading = ParagraphStyle(
        "heading",
        parent=style,
        fontName="KharchaReportBold",
        fontSize=11,
        leading=16,
        spaceBefore=16,
        spaceAfter=8,
        keepWithNext=True,
    )
    right = ParagraphStyle("right", parent=style, alignment=TA_RIGHT)

    def p(text, use_style=style):
        return Paragraph(escape(readable(text)), use_style)

    def table(headers, entries, widths):
        content = [[p(h, small) for h in headers]] + entries
        result = LongTable(content, colWidths=widths, repeatRows=1, splitInRow=1, hAlign="LEFT")
        result.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), color("secondary")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, color("border")),
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
        p("Spending report", title),
        p(f"{options.start} to {options.end} (inclusive)", small),
        p("Applied filters: " + "; ".join(filters), small),
        Spacer(1, 14),
    ]
    if options.category != "all" or options.group:
        story.append(
            p(
                "Matching payments; totals include their full personal shares, including other split categories.",
                small,
            )
        )
    if store.get_setting("demo", {}).get("as_of"):
        story.insert(0, p("SYNTHETIC DEMO - Fictional data, no real payments", small))
    for currency, item in sorted(data.items()):
        story.append(DashboardOpening(currency, item, body_width))
        story.append(MetricStrip(currency, item, body_width))
        # Never drop additional months for long ranges: use further chart panels.
        months = month_totals(item)
        for offset in range(6, len(months), 6):
            story.append(MonthlyContinuation(currency, months[offset : offset + 6], body_width))
        right_width = body_width * 0.47 - 24
        compact_months = any(
            chart_money(value, currency, right_width / min(6, len(months)) - 2)
            != display_money(value, currency)
            for _, value in months
        )
        compact_groups = any(
            chart_money(value, currency, body_width * 0.53 / 3 - 8, size=10)
            != display_money(value, currency)
            for value in item["groups"].values()
        )
        if compact_months or compact_groups:
            story.append(
                p(
                    "Large chart and legend labels use K/M/B/T abbreviations. Exact figures are shown below.",
                    small,
                )
            )
            story.append(
                table(
                    ["Month", "Exact net spending"],
                    [[p(month), p(money(value, currency), right)] for month, value in months],
                    [body_width - 190, 190],
                )
            )
            story.append(
                p(
                    "Exact group spending: "
                    + " | ".join(
                        label + ": " + money(item["groups"].get(key, 0), currency)
                        for label, key in (
                            ("Regular", "other"),
                            ("Fixed", "fixed"),
                            ("Unavoidable", "unavoidable"),
                        )
                    ),
                    small,
                )
            )
        if item["review"]:
            warning = p(
                f"Provisional: {item['review']} matching transactions need evidence review.", small
            )
            notice = LongTable([[warning]], colWidths=[body_width])
            notice.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), color("surface-warm")),
                        ("BOX", (0, 0), (-1, -1), 0.5, color("surface-warm-border")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                )
            )
            story.extend([Spacer(1, 6), notice])
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
            signed = any(amount < 0 for _, amount in entries)
            story.append(
                p(
                    "Net spending after refunds. "
                    + (
                        "Tracks show refunds left of zero and spending right of zero."
                        if signed
                        else "Tracks show relative net spending in this selection."
                    ),
                    small,
                )
            )
            story.append(Spacer(1, 6))
            label_width = body_width - 150
            rows = []
            for label, amount in entries:
                rows.append(
                    [
                        CategoryBadge(label if name.startswith("Category") else "Payment account"),
                        [
                            p(label),
                            SpendingBar(
                                amount, maximum, label_width - 51, category_color(label), signed
                            ),
                        ],
                        p(money(amount, currency), right),
                    ]
                )
            story.append(
                table(
                    ["", "Category" if name.startswith("Category") else "Account", "Net spending"],
                    rows,
                    [35, label_width - 35, 150],
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
    story.extend(
        [
            p("About this report", heading),
            p(
                "Total personal spending includes fixed and unavoidable costs, personal split shares and refunds received in this period. Income, transfers, card repayments and excluded payments contribute zero.",
                small,
            ),
            p(
                "Payment accounts are the recorded accounts, not verified payment methods. Unsupported text characters appear as [U+code] so no label is silently omitted.",
                small,
            ),
            p(
                "Generated "
                + now()[:19].replace("T", " ")
                + " IST. Based on recorded ledger data; coverage may be incomplete.",
                small,
            ),
        ]
    )
    if options.category != "all" or options.group:
        story.append(
            p(
                "Category and spending-group filters select matching payments. Totals include each matching payment's full personal share, including other split categories.",
                small,
            )
        )
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=78,
        bottomMargin=45,
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
        canvas.setFillColor(color("background"))
        canvas.rect(0, 0, width, height, fill=1, stroke=0)
        # Page chrome repeats the app's compact brand header, without navigation controls.
        from reportlab.lib.utils import ImageReader

        logo = Path(__file__).resolve().parent / "assets/kharcha-report-icon.png"
        canvas.drawImage(ImageReader(str(logo)), margin, height - 54, 26, 26, mask="auto")
        fitted_text(canvas, "Kharcha", margin + 34, height - 38, 130, size=13, bold=True)
        fitted_text(
            canvas,
            "personal spending",
            margin + 34,
            height - 51,
            130,
            size=7,
            tone="muted-foreground",
        )
        canvas.setStrokeColor(color("border"))
        canvas.setLineWidth(0.5)
        canvas.line(margin, height - 65, width - margin, height - 65)
        canvas.line(margin, 39, width - margin, 39)
        canvas.setFont("KharchaReport", 7)
        canvas.setFillColor(color("muted-foreground"))
        canvas.drawString(margin, 25, f"{options.start} to {options.end}")
        canvas.drawRightString(width - margin, 25, f"Page {document.page} of {page_count}")
        canvas.restoreState()

    stream.seek(0)
    stream.truncate()
    final_doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=78,
        bottomMargin=45,
        title="Kharcha spending report",
        author="Kharcha",
    )
    final_doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
