"""Evidence-first local parsers. Unknown templates are deliberately reviewable."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from dateutil import parser as dates
from .categorization import merchant_match
from .email_auth import gmail_authentication

IST = ZoneInfo("Asia/Kolkata")
VERSION = "local-7"
MONEY = re.compile(
    r"(?<!\w)(INR|Rs\.?|₹|USD|US\$|EUR|€|GBP|£)\s*([\d,]+(?:\.\d{1,2})?)(?![\d.])", re.I
)
FINANCIAL = re.compile(
    r"debit|credit|transaction|payment|paid|spent|purchase|receipt|refund|reversal|transfer|\bemi\b|instalment|installment|withdraw|statement|salary|NEFT|IMPS|UPI|wallet",
    re.I,
)


def minor(value) -> int:
    try:
        number = Decimal(str(value).replace(",", "")) * 100
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError("Amount must have at most two decimal places")
        return int(number)
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid amount") from None


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        # HTML email often omits </head>; <body> implicitly closes it.
        if tag == "body":
            self.in_head = False
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "tr", "td", "li", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden and not self.in_head:
            self.parts.append(data)


def plaintext(html):
    p = TextHTML()
    p.feed(html)
    return re.sub(r"[ \t]+", " ", "".join(p.parts)).strip()


def decode_message(message):
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    texts, htmls, attachments = [], [], []

    def visit(part):
        if part.get("filename"):
            attachments.append(part["filename"])
            return
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
                "utf-8", "replace"
            )
            if part.get("mimeType") == "text/plain":
                texts.append(text)
            elif part.get("mimeType") == "text/html":
                htmls.append(plaintext(text))
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    # multipart/alternative contains two representations of the same transaction.
    body = "\n".join(texts or htmls)
    received = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, IST).isoformat()
    sender = parseaddr(headers.get("from", ""))[1].lower()
    return {
        "id": message["id"],
        "sender": sender,
        "subject": headers.get("subject", ""),
        "received_at": received,
        "body": body,
        "attachments": attachments,
        "authentication": gmail_authentication(payload.get("headers", []), sender),
    }


def fingerprint(sender, text):
    normalized = re.sub(r"\b[\w.+-]+@[\w.-]+\b", "<address>", text.lower())
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized)[:2000]
    return hashlib.sha256((sender + normalized).encode()).hexdigest()[:16]


def get_date(text, received):
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
        r"\b\d{1,2}[ -](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ ,-]+\d{2,4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s*\d{4}\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            prefix = text[max(0, match.start() - 25) : match.start()].lower()
            if any(x in prefix for x in ("due date", "pay by", "statement period")):
                continue
            try:
                return dates.parse(
                    match.group(), dayfirst=not re.match(r"\d{4}-", match.group())
                ).date().isoformat(), "explicit"
            except (ValueError, OverflowError):
                pass
    return datetime.fromisoformat(received).astimezone(IST).date().isoformat(), "received"


def parse_email(email, _allow_lines=True):
    subject, body = email.get("subject", ""), email.get("body", "")
    # Work with readable MIME text; the original decoded body stays encrypted.
    body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
    body = re.sub(r"[ \t\u200b\u200c\u00a0]+", " ", body)
    text = subject + "\n" + body
    base = {"version": VERSION, "template": fingerprint(email.get("sender", ""), text)}
    if not FINANCIAL.search(text):
        return {
            **base,
            "status": "excluded",
            "reason": "No financial transaction evidence",
            "observations": [],
        }
    if re.search(
        r"we value your feedback|fund transfer limit updated|activate upi|ensure smooth.*international.*payments|introducing trip emi",
        subject,
        re.I,
    ):
        return {
            **base,
            "status": "excluded",
            "reason": "Bank service or promotional notice; no completed transaction",
            "observations": [],
        }
    if re.search(
        r"(?:transaction|payment).{0,100}(?:has failed|declined)|(?:EMI|payment) due on|standing instruction.{0,100}(?:cancelled|canceled)|new pin generation|cooling period implemented|consent management update",
        subject + "\n" + body[:500],
        re.I,
    ):
        return {
            **base,
            "status": "excluded",
            "reason": "Failed payment, reminder or account-service notice; no completed transaction",
            "observations": [],
        }
    # Never treat requests, OTPs or statements as a payment confirmation.
    negative = [
        r"(?:one.time password|\bOTP\b|verification code)(?:\s*(?:is|:)?\s*\d{4,8})?",
        r"transaction (?:has )?failed|payment (?:has )?failed|payment declined|transaction declined|unsuccessful",
        r"payment request|collect request|request(?:ed)? (?:you )?to pay",
        r"minimum amount due|total amount due|payment due|statement (?:is |for |ready)|bill is ready",
        r"upcoming payment|will be (?:debited|charged)|is due on|scheduled (?:payment|debit)",
    ]
    if re.search(
        r"upcoming payment|will be (?:debited|charged)|is due on", subject + "\n" + body[:600], re.I
    ):
        return {
            **base,
            "status": "excluded",
            "reason": "Upcoming payment notification; no completed transaction",
            "observations": [],
        }
    for pattern in negative:
        if re.search(pattern, subject, re.I) or (
            re.search(pattern, body[:300], re.I)
            and not re.search(r"debited|credited|spent|successfully paid", body[:300], re.I)
        ):
            return {
                **base,
                "status": "review" if email.get("attachments") else "excluded",
                "reason": "Statement attachment requires manual review"
                if email.get("attachments")
                else "Request, statement, failed payment or authentication message",
                "observations": [],
            }
    bank = parse_bank_alert(email, body)
    if bank:
        return {**base, **bank}
    amounts = list(MONEY.finditer(text))
    # Available balance, limit and amount due are not transaction amounts.
    amounts = [
        m
        for m in amounts
        if not re.search(
            r"(?:available|avl|balance|limit|amount due|minimum due|total due)[^\n]{0,35}$",
            text[max(0, m.start() - 60) : m.start()],
            re.I,
        )
    ]
    if not amounts:
        return {
            **base,
            "status": "review",
            "reason": "No unambiguous currency and amount",
            "observations": [],
        }
    success = re.search(
        r"debited|credited|debit of|credit of|spent|paid|converted to (?:an? )?EMI|payment (?:of .{0,30})?(?:is |was )?(?:successful|received)|purchase (?:of|at|for)|refund(?:ed)?|withdrawn|charged|EMI (?:of|debited|charged)|transaction (?:of|at)",
        text,
        re.I,
    )
    if not success:
        return {
            **base,
            "status": "review",
            "reason": "Payment completion is not established",
            "observations": [],
        }
    # Repeated values in subject/body are one amount. Distinct values need review,
    # except explicit total + original FX amounts, which are retained for review.
    distinct = {
        (
            m.group(1).upper().replace("RS.", "INR").replace("RS", "INR").replace("₹", "INR"),
            minor(m.group(2)),
        )
        for m in amounts
    }
    if len(distinct) > 1:
        # Support independent lines with their own transaction reference.
        candidates = []
        for line in body.splitlines() if _allow_lines else []:
            if MONEY.search(line) and re.search(
                r"(?:ref|UTR|RRN|transaction id)\s*[:#.-]?\s*[A-Z0-9/-]{6,}", line, re.I
            ):
                result = parse_email({**email, "subject": "", "body": line}, _allow_lines=False)
                candidates.extend(result["observations"])
        if len(candidates) > 1 and len(
            {(o["reference"], o["amount_minor"]) for o in candidates}
        ) == len(candidates):
            return {
                **base,
                "status": "parsed",
                "reason": "Independent referenced transaction lines",
                "observations": candidates,
            }
        return {
            **base,
            "status": "review",
            "reason": "Multiple different amounts; identify the transaction amount",
            "observations": [],
        }
    currency, amount = next(iter(distinct))
    currency = {"US$": "USD", "€": "EUR", "£": "GBP"}.get(currency, currency)
    direction, kind = "debit", "purchase"
    if re.search(r"EMI.*(?:conver|revers)|(?:conver|revers).*EMI", text, re.I):
        kind = "financing_adjustment"
    elif re.search(
        r"(?:convert(?:ed)?|financ(?:ed|ing)).{0,60}(?:EMI|instal)|(?:EMI|instal).{0,60}convert",
        text,
        re.I,
    ):
        kind = "financed_purchase"
    elif re.search(r"refund|reversal|reversed", subject + "\n" + body[:800], re.I):
        kind, direction = "refund", "credit"
    elif re.search(
        r"credit card (?:bill )?payment|card bill|payment (?:received|towards|to).{0,45}(?:credit card|card ending)|repayment.*card",
        text,
        re.I,
    ):
        kind = "card_repayment"
    elif re.search(r"\bEMI\b|instalment|installment", text, re.I):
        kind = "emi"
    elif re.search(r"cash withdrawal|ATM withdrawal|withdrawn.*ATM", text, re.I):
        kind = "cash_withdrawal"
    elif re.search(
        r"wallet.{0,20}(?:load|top.?up|added)|(?:added|loaded).{0,30}wallet", text, re.I
    ):
        kind = "wallet_funding"
    elif re.search(r"\bSIP\b|mutual fund|investment (?:purchase|contribution)", text, re.I):
        kind = "investment"
    elif re.search(r"credited|credit of|received (?:INR|Rs|₹)|salary", text, re.I):
        kind, direction = "income", "credit"
    elif re.search(r"fee|interest charged|finance charge", subject, re.I):
        kind = "fee"
    elif re.search(r"\bUPI\b|NEFT|IMPS|transferred|sent to", text, re.I):
        kind = "person_payment"
    account = re.search(
        r"(?:a/c|acct|account|card)\s*(?:no\.?|number|ending(?: in)?|ending with|xx|x|\*|:|-|\s)*([xX*•]*\d{4})(?!\d)",
        text,
        re.I,
    )
    domain = email.get("sender", "").split("@")[-1]
    bank = next(
        (
            bank
            for bank in (
                "hdfc",
                "icici",
                "kotak",
                "axis",
                "sbi",
                "amex",
                "idfc",
                "indusind",
                "yesbank",
                "hsbc",
                "rbl",
                "federal",
                "bob",
            )
            if bank in domain
        ),
        domain,
    )
    account_label = (bank.upper() + " •" + account.group(1)[-4:]) if account else "Unknown account"
    ref = re.search(
        r"(?:UPI\s*(?:Ref(?:erence)?(?:\s*(?:no|number))?|transaction id)|UTR|RRN|(?:transaction|txn)\s*(?:id|ref(?:erence)?(?:\s*(?:no|number))?)|reference\s*(?:no|number)?|ref(?:\s*no)?)[\s.:#-]*([A-Z0-9][A-Z0-9/-]{5,})",
        text,
        re.I,
    )
    vpa = re.search(
        r"\b([a-z0-9._-]+@(?:okaxis|okhdfcbank|okicici|oksbi|ybl|ibl|axl|paytm|upi|apl|ptyes|axisbank|icici|sbi|hdfcbank))\b",
        body,
        re.I,
    )
    person = re.search(
        r"(?:paid to|sent to|transferred to|to merchant|merchant\s*[:=-]|at merchant|towards|\bat\b|\bto\b)\s+([A-Z][^\n]{1,70})",
        body,
        re.I,
    )
    name = person.group(1) if person else "Unknown recipient"
    name = re.split(r"\s+(?:on|using|via|from|with|ref|transaction|at)\b|[.;]\s", name, flags=re.I)[
        0
    ].strip(" :.-")
    if vpa and not merchant_match(name):
        name = vpa.group(1).lower()
    identity = "vpa:" + vpa.group(1).lower() if vpa else None
    date, date_basis = get_date(body, email["received_at"])
    warnings = []
    if date_basis == "received":
        warnings.append("Transaction date missing; email received date used")
    if direction == "credit" and kind == "income":
        warnings.append("Incoming credit: confirm income, refund, transfer or repayment")
    if kind == "card_repayment":
        warnings.append("Card repayment excluded; underlying purchases may be missing")
    if not account:
        warnings.append("Account could not be identified")
    if not identity and kind == "person_payment":
        warnings.append("Confirm the person before creating an automatic rule")
    observation = {
        "amount_minor": amount,
        "currency": currency,
        "direction": direction,
        "kind": kind,
        "date": date,
        "date_basis": date_basis,
        "counterparty": name,
        "counterparty_key": identity,
        "identity_confirmed": bool(identity),
        "account": account_label,
        "reference": ref.group(1).upper() if ref else None,
        "reference_namespace": "upi" if re.search(r"UPI|UTR|RRN", text, re.I) else bank,
        "category": "EMIs"
        if kind == "emi"
        else "Fees & interest"
        if kind == "fee"
        else "Uncategorized",
        "warnings": warnings,
        "evidence": body[:4000],
    }
    return {
        **base,
        "status": "parsed",
        "reason": "Local evidence parser; template audit pending",
        "observations": [observation],
    }


def has_transaction_evidence(email, result):
    """Require payment evidence before retaining a broadly scanned message.

    This extra check applies to automatic mail sync. A personal conversation
    mentioning an amount and 'paid' is not by itself a receipt or bank alert.
    Sender domains and subject formats do not determine eligibility.
    """
    if result["status"] != "parsed" or not result["observations"]:
        return False
    if result.get("audited"):
        return True
    body = email.get("body", "")
    claim = re.sub(r"\b(I|we)['’]ve\b", r"\1 have", body[:1000], flags=re.I)
    claim = re.sub(r"\b(was|were|is|are)n['’]t\b", r"\1 not", claim, flags=re.I)
    if not MONEY.search(body):
        return False
    if re.search(r"\bif\s+" + MONEY.pattern, claim, re.I):
        return False
    if re.search(
        r"\b(?:unpaid|not\s+(?:(?:yet|been)\s+)*(?:paid|charged|debited|credited|completed))\b"
        r"|\b(?:haven['’]t|hasn['’]t|didn['’]t|never)\s+(?:been\s+)?(?:paid|pay|spent|spend)\b"
        r"|\b(?:will|would|should|could|might|plan to|planning to|want to|need to)\s+"
        r"(?:be\s+)?(?:pay|spend|purchase|transfer|charge|debit|credit)\b"
        r"|\b(?:I|we)\s+(?:(?:have|had|just|already|recently)\s+)*"
        r"(?:paid|spent|transferred|sent|purchased|bought)\b",
        claim,
        re.I,
    ):
        return False
    if not re.search(
        r"\b(?:debited|credited|charged|withdrawn|refunded|reversed|paid|spent)\b"
        r"|\bpayment\s+(?:of\s+.{0,40}?)?(?:is\s+|was\s+|has been\s+)?"
        r"(?:successful|received|completed|confirmed)\b"
        r"|\bconverted\s+to\s+(?:an?\s+)?EMI\b",
        body,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"(?:a/c|acct|account|card)\s*(?:no\.?|number|ending(?: in)?|ending with|xx|x|\*|:|-|\s)*"
            r"[xX*•]*\d{4}(?!\d)"
            r"|\b(?:UTR|RRN|UPI\s*ref(?:erence)?|(?:transaction|txn|payment)\s*(?:id|ref(?:erence)?)|"
            r"reference|ref|(?:receipt|invoice|order)\s*(?:id|number|no\.?|#))"
            r"\s*[:#.-]?\s*[A-Z0-9][A-Z0-9/-]{5,}\b",
            body,
            re.I,
        )
    )


def parse_bank_alert(email, body):
    """Narrow formats checked against fetched, original financial alerts."""
    domain = email.get("sender", "").rsplit("@", 1)[-1].lower()

    def domain_is(name):
        return domain == name or domain.endswith("." + name)

    observation = None
    template = None
    flat = re.sub(r"\s+", " ", body)

    def event(
        amount,
        account,
        merchant,
        date,
        kind="purchase",
        direction="debit",
        reference=None,
        namespace="",
        time=None,
    ):
        parsed_date, basis = get_date(date or "", email["received_at"])
        return {
            "amount_minor": minor(amount),
            "currency": "INR",
            "direction": direction,
            "kind": kind,
            "date": parsed_date,
            "date_basis": basis,
            "time": time,
            "counterparty": merchant,
            "account": account,
            "reference": reference,
            "reference_namespace": namespace,
            "category": "Uncategorized",
            "warnings": ["Transaction date missing; email received date used"]
            if basis == "received"
            else [],
            "evidence": body[:4000],
        }

    if domain_is("axis.bank.in") or domain_is("axisbank.com"):
        fields = re.search(
            r"Amount (Debited|Credited):\s*(INR|Rs\.?)\s*([\d,.]+)\s*Account Number:\s*([Xx*]+\d{4})\s*Date & Time:\s*(\d{2}-\d{2}-\d{2,4}),\s*(\d{2}:\d{2}:\d{2})\s*IST\s*Transaction Info:\s*([^\n]+)",
            body,
            re.I,
        )
        if fields:
            flow, currency, amount, account, date, time, info = fields.groups()
            direction = "debit" if flow.lower() == "debited" else "credit"
            reference = re.search(
                r"^(?:UPI/(?:P2A|P2M|P2P)/|NEFT/|IMPS/)([A-Z0-9-]+)/(.+)", info, re.I
            )
            observation = {
                "amount_minor": minor(amount),
                "currency": "INR",
                "direction": direction,
                "kind": "person_payment" if direction == "debit" else "income",
                "date": get_date(date, email["received_at"])[0],
                "time": time,
                "date_basis": "explicit",
                "counterparty": reference.group(2).strip() if reference else info,
                "account": "AXIS •" + account[-4:],
                "reference": reference.group(1).upper() if reference else None,
                "reference_namespace": "upi" if info.upper().startswith("UPI/") else "axis",
                "category": "Uncategorized",
                "warnings": [],
                "evidence": body[:4000],
            }
            if direction == "credit":
                observation["warnings"].append(
                    "Incoming credit: confirm income, refund, transfer or repayment"
                )
            if direction == "debit" and merchant_match(observation["counterparty"]):
                observation["kind"] = "purchase"
            if direction == "debit" and observation["counterparty"].strip().casefold() in (
                "cred",
                "cred club",
            ):
                observation["kind"] = "card_repayment"
                observation["warnings"].append(
                    "CRED payment treated as a card repayment; confirm against its payment receipt"
                )
            if not reference:
                observation["warnings"].append("Transaction reference not identified")
            template = "axis:account-alert-v1"
        else:
            fields = re.search(
                r"A/c no\.\s*([Xx*]+\d{4}) has been (debited|credited) with INR\s*([\d,.]+) on (\d{2}-\d{2}-\d{4})\s+(?:at\s+)?(\d{2}:\d{2}:\d{2}) IST by ([^.]+)\.",
                flat,
                re.I,
            )
            if fields:
                account, flow, amount, date, time, info = fields.groups()
                ref = re.search(
                    r"^(?:UPI/(?:P2A|P2M|P2P)/|NEFT/|IMPS/)([A-Z0-9-]+)/(.+)", info, re.I
                )
                credit = flow.lower() == "credited"
                observation = event(
                    amount,
                    "AXIS •" + account[-4:],
                    ref.group(2).strip() if ref else info,
                    date,
                    "income" if credit else "person_payment",
                    "credit" if credit else "debit",
                    reference=ref.group(1).upper() if ref else None,
                    namespace="upi" if info.upper().startswith("UPI/") else "axis",
                    time=time,
                )
                if credit:
                    observation["warnings"].append(
                        "Incoming credit: confirm income, refund, transfer or repayment"
                    )
                if not credit and info.upper().startswith("PPF/"):
                    observation.update(
                        kind="investment", counterparty="PPF contribution", reference=info
                    )
                if not credit and re.fullmatch(
                    r"Credit\s*Card Payment\s+[Xx* ]*\d{4}", info.strip(), re.I
                ):
                    observation["kind"] = "card_repayment"
                template = "axis:account-paragraph-v1"
            elif "has been successfully credited to the beneficiary" in flat:
                return {
                    "status": "review",
                    "reason": "NEFT delivery advice: match to the outgoing bank debit before counting",
                    "template": "axis:neft-delivery-advice-v1",
                    "observations": [],
                }
        card = re.search(
            r"Transaction Amount:\s*INR\s*([\d,.]+)\s*Merchant Name:\s*(.+?)\s*Axis Bank Credit Card No\.\s*[Xx*]*(\d{4})\s*Date & Time:\s*(\d{2}-\d{2}-\d{4}),\s*(\d{2}:\d{2}:\d{2}) IST",
            flat,
            re.I,
        )
        if card:
            amount, merchant, account, date, time = card.groups()
            observation = event(
                amount, "AXIS •" + account, merchant, date, namespace="axis-card", time=time
            )
            template = "axis:card-table-v1"
    if domain_is("yes.bank.in") or domain_is("yesbank.in"):
        fields = re.search(
            r"INR\s*([\d,.]+)\s+has been spent on your YES BANK Credit Card ending with\s+(\d{4})\s+at\s+(.+?)\s+on\s+(\d{2}-\d{2}-\d{4})\s+at\s+(\d{2}:\d{2}:\d{2}\s*[ap]m)",
            body,
            re.I,
        )
        if fields:
            amount, account, merchant, date, time = fields.groups()
            observation = {
                "amount_minor": minor(amount),
                "currency": "INR",
                "direction": "debit",
                "kind": "purchase",
                "date": get_date(date, email["received_at"])[0],
                "time": datetime.strptime(time.upper(), "%I:%M:%S %p").strftime("%H:%M:%S"),
                "date_basis": "explicit",
                "counterparty": merchant,
                "account": "YES BANK •" + account,
                "reference": None,
                "reference_namespace": "yesbank",
                "category": "Uncategorized",
                "warnings": [],
                "evidence": body[:4000],
            }
            template = "yesbank:card-spend-v1"
    if domain_is("kotak.bank.in") or domain_is("kotak.com"):
        fields = re.search(
            r"INR\s*([\d,.]+) spent at (.+?) on (\d{2}/\d{2}/\d{2,4})\s+at (\d{2}:\d{2}:\d{2}\s*[AP]M) using your Kotak Credit Card [xX*]*(\d{4})",
            flat,
            re.I,
        )
        if fields:
            amount, merchant, date, time, account = fields.groups()
            ref = re.search(r"^UPI-K-(\d{12})-", merchant, re.I)
            observation = event(
                amount,
                "KOTAK •" + account,
                merchant,
                date,
                reference=ref.group(1) if ref else None,
                namespace="upi" if ref else "kotak",
                time=datetime.strptime(time.upper(), "%I:%M:%S %p").strftime("%H:%M:%S"),
            )
            template = "kotak:card-spend-v1"
        else:
            fields = re.search(
                r"Thank you for your payment of Rs\.\s*([\d,.]+) for your Kotak Credit Card ending with [xX*]*(\d{4}) on (\d{2}-[A-Za-z]{3}-\d{4})",
                flat,
                re.I,
            )
            if fields:
                amount, account, date = fields.groups()
                observation = event(
                    amount,
                    "KOTAK •" + account,
                    "Kotak card repayment",
                    date,
                    "card_repayment",
                    "credit",
                    namespace="kotak",
                )
                template = "kotak:repayment-v1"
    if domain_is("icici.bank.in") or domain_is("icicibank.com"):
        if re.search(
            r"successfully processed your payment of INR.+with Standing Instruction", flat, re.I
        ):
            return {
                "status": "review",
                "reason": "Successful recurring-payment notice: match to the card debit before counting; mandate ID is not a transaction reference",
                "template": "icici:standing-instruction-v1",
                "observations": [],
            }
        fields = re.search(
            r"ICICI Bank Credit Card [Xx*]*(\d{4}) has been used for a transaction of INR\s*([\d,.]+) on ([A-Za-z]{3}\s+\d{1,2},\s*\d{4}) at (\d{2}:\d{2}:\d{2})\.\s*Info:\s*(.+?)\.\s*The Available Credit Limit",
            flat,
            re.I,
        )
        if fields:
            account, amount, date, time, merchant = fields.groups()
            observation = event(
                amount, "ICICI •" + account, merchant, date, namespace="icici", time=time
            )
            template = "icici:card-spend-v1"
        else:
            fields = re.search(
                r"received payment of INR\s*([\d,.]+) on your ICICI Bank Credit Card account (?:[\dXx* ]+?)\s+(\d{4}) on (\d{1,2}-[A-Za-z]{3}-\d{4})",
                flat,
                re.I,
            )
            if fields:
                amount, account, date = fields.groups()
                observation = event(
                    amount,
                    "ICICI •" + account,
                    "ICICI card repayment",
                    date,
                    "card_repayment",
                    "credit",
                    namespace="icici",
                )
                template = "icici:repayment-v1"
    if domain_is("federalbank.co.in") and email.get("sender", "").startswith("scapiacards@"):
        fields = re.search(
            r"Your payment on (\d{2}-\d{2}-\d{4}) at (\d{1,2}:\d{2}\s*[AP]M) using your Scapia Federal (?:Visa|RuPay) Credit Card ending in (\d{4}) has been successfully processed\.\s*Amount\s*₹\s*([\d,.]+)\s*Merchant\s+(.+?)(?:\s+Want to convert|\s+Not you\?|$)",
            flat,
            re.I,
        )
        if fields:
            date, time, account, amount, merchant = fields.groups()
            observation = event(
                amount,
                "SCAPIA •" + account,
                merchant,
                date,
                namespace="scapia",
                time=datetime.strptime(time.upper(), "%I:%M %p").strftime("%H:%M:%S"),
            )
            template = "scapia:card-spend-v1"
        else:
            fields = re.search(
                r"received your payment of ₹\s*([\d,.]+) towards your Scapia Federal Credit Card",
                flat,
                re.I,
            )
            if fields:
                observation = event(
                    fields.group(1),
                    "Unknown account",
                    "Scapia card repayment",
                    None,
                    "card_repayment",
                    "credit",
                    namespace="scapia",
                )
                template = "scapia:repayment-v1"
    if domain_is("cred.club") and "your credit card payment was successful" in flat.lower():
        fields = re.search(
            r"([A-Za-z ]+?Bank)\s*[•*]+\s*(\d{4})\s*payment details\s*amount paid\s*₹\s*([\d,.]+)\s*payment date\s*([A-Za-z]{3}\s+\d{1,2},\s*\d{4})",
            flat,
            re.I,
        )
        ref = re.search(r"Order ID:\s*([A-Z0-9]+)", flat, re.I)
        if fields and ref:
            bank, account, amount, date = fields.groups()
            observation = event(
                amount,
                bank.strip() + " •" + account,
                "CRED card repayment",
                date,
                "card_repayment",
                "credit",
                reference=ref.group(1),
                namespace="cred",
            )
            observation["warnings"].append(
                "Card repayment excluded; match any bank debit and verify underlying purchases"
            )
            template = "cred:card-repayment-v1"
    if domain_is("sbi.bank.in"):
        fields = re.search(
            r"Your A/C\s*([Xx*]+\d{4,6})\s*Debited INR\s*([\d,.]+) on (\d{2}/\d{2}/\d{2,4})\s*-Transferred to (.+?)\.\s*Avl Balance",
            flat,
            re.I,
        )
        if fields:
            account, amount, date, recipient = fields.groups()
            observation = event(
                amount, "SBI •" + account[-4:], recipient, date, "person_payment", namespace="sbi"
            )
            observation["warnings"].append(
                "Confirm whether this transfer is personal spending, an owned-account transfer, or a loan installment"
            )
            template = "sbi:transfer-debit-v1"
    if observation:
        return {
            "status": "parsed",
            "reason": "Structured bank alert; amount, account, date and recipient extracted",
            "template": template,
            "audited": True,
            "observations": [observation],
        }
    return None
