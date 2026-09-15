"""Conservative interpretation of Gmail's receiving-server authentication.

Only original Gmail API headers are input here, never text from an email body.
RFC 8601 requires the receiving boundary to remove forged Authentication-Results
claiming its own authserv-id. Multiple/ambiguous results fail closed. Header-only
plugin exports without this provenance remain reviewable, not automatic spending.
"""

from email.utils import getaddresses
import re

UNVERIFIED = "Email sender authentication is missing, failed, or ambiguous; verify the payment before adding it"


def gmail_authentication(headers, sender):
    unknown = {"verified": False}
    if not isinstance(headers, list) or any(
        not isinstance(h, dict)
        or not isinstance(h.get("name"), str)
        or not isinstance(h.get("value"), str)
        for h in headers
    ):
        return unknown
    from_headers = [h["value"] for h in headers if h["name"].lower() == "from"]
    addresses = getaddresses(from_headers)
    if len(from_headers) != 1 or len(addresses) != 1 or addresses[0][1].lower() != sender.lower():
        return unknown
    domain = sender.rsplit("@", 1)[-1].lower()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return unknown
    received = [h["value"] for h in headers if h["name"].lower() == "received"]
    if not any(re.search(r"\bby\s+mx\.google\.com\s", value, re.I) for value in received):
        return unknown
    results = [h["value"] for h in headers if h["name"].lower() == "authentication-results"]
    trusted = [value for value in results if re.match(r"^\s*mx\.google\.com\s*;", value, re.I)]
    if len(trusted) != 1 or not results or results[0] != trusted[0]:
        return unknown
    value = trusted[0]
    # Comments cannot supply methods/properties. Remove nested RFC comments;
    # reject unsupported quoting rather than guessing about ambiguous input.
    while re.search(r"\([^()]*\)", value):
        value = re.sub(r"\([^()]*\)", " ", value)
    if any(c in value for c in '()"\\'):
        return unknown
    methods = value.lower().split(";")[1:]
    dmarc = [m.strip() for m in methods if re.match(r"^\s*dmarc\s*=", m)]
    if len(dmarc) != 1 or not re.match(r"dmarc\s*=\s*pass\b", dmarc[0]):
        return unknown
    from_domains = re.findall(r"\bheader\.from\s*=\s*([a-z0-9.-]+)(?=\s|$)", dmarc[0])
    if from_domains != [domain]:
        return unknown
    # DMARC supplies alignment. Require an actual SPF or DKIM pass as well,
    # and never treat ARC/forwarded assertions as standalone authentication.
    if not any(re.match(r"^\s*(?:dkim|spf)\s*=\s*pass\b", m) for m in methods):
        return unknown
    if any(re.match(r"^\s*arc\s*=\s*fail\b", m) for m in methods):
        return unknown
    return {"verified": True, "domain": domain, "receiver": "mx.google.com"}
