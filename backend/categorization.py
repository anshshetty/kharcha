"""Local merchant defaults, below user corrections and saved category rules.

Match complete merchant names/aliases or explicitly supported bank abbreviations,
never payment providers alone. These are suggestions, not transaction verification.
"""

from __future__ import annotations

import html
import re
import unicodedata

VERSION = "merchants-3"

KIND_CATEGORIES = {
    "income": "Income",
    "card_repayment": "Card repayments",
    "investment": "Investments",
    "own_transfer": "Own transfers",
    "wallet_funding": "Wallet funding",
    "cash_withdrawal": "Cash withdrawals",
    "reimbursement": "Reimbursements",
    "lending": "Lending",
    "emi": "EMIs",
    "fee": "Fees & interest",
}

# Specific services precede their parent brand. Aliases describe statement
# merchant names, not email senders or arbitrary mentions in an email body.
MERCHANTS = [
    ("Amazon Grocery", "Groceries", ("amazon pay grocery", "amazon pay in grocery")),
    ("Swiggy Instamart", "Groceries", ("swiggy instamart", "instamart")),
    ("Swiggy Dineout", "Food & dining", ("swiggy dineout", "dineout")),
    ("Zepto Cafe", "Food & dining", ("zepto cafe", "zeptocafe")),
    ("Amazon Fresh", "Groceries", ("amazon fresh", "amazonfresh")),
    ("Amazon Prime", "Subscriptions", ("amazon prime", "amazonprime", "prime video")),
    ("Uber Eats", "Food & dining", ("uber eats", "ubereats")),
    ("Ola Foods", "Food & dining", ("ola foods",)),
    ("Swiggy", "Food & dining", ("swiggy", "swiggyfood", "bundl technologies")),
    ("Zomato", "Food & dining", ("zomato", "zomatofood")),
    ("Domino’s", "Food & dining", ("dominos", "domino s")),
    ("McDonald’s", "Food & dining", ("mcdonalds", "mcdonald s")),
    ("Starbucks", "Food & dining", ("starbucks", "tata starbucks", "starbuckswestpier")),
    ("KFC", "Food & dining", ("kfc",)),
    ("Pizza Hut", "Food & dining", ("pizza hut",)),
    ("Burger King", "Food & dining", ("burger king",)),
    ("Blinkit", "Groceries", ("blinkit", "grofers")),
    ("Zepto", "Groceries", ("zepto", "zeptonow", "zeptomarketplace")),
    ("BigBasket", "Groceries", ("bigbasket", "big basket", "bbnow")),
    ("DMart", "Groceries", ("dmart", "d mart")),
    ("JioMart", "Groceries", ("jiomart", "jio mart")),
    ("Zara", "Shopping", ("zara",)),
    ("Myntra", "Shopping", ("myntra",)),
    ("Flipkart", "Shopping", ("flipkart",)),
    ("Amazon", "Shopping", ("amazon", "amzn")),
    ("H&M", "Shopping", ("h m", "h and m")),
    ("Uniqlo", "Shopping", ("uniqlo",)),
    ("AJIO", "Shopping", ("ajio",)),
    ("Nykaa", "Shopping", ("nykaa",)),
    ("FirstCry", "Shopping", ("firstcry", "first cry")),
    ("Decathlon", "Shopping", ("decathlon",)),
    ("Nike", "Shopping", ("nike",)),
    ("Adidas", "Shopping", ("adidas",)),
    ("Croma", "Shopping", ("croma",)),
    ("IKEA", "Rent & home", ("ikea",)),
    ("Urban Company", "Rent & home", ("urban company", "urbancompany", "urbanclap")),
    ("Uber", "Transport", ("uber",)),
    ("Ola", "Transport", ("ola cabs", "olacabs", "ola")),
    ("Rapido", "Transport", ("rapido",)),
    ("Namma Yatri", "Transport", ("namma yatri", "nammayatri")),
    ("MakeMyTrip", "Travel", ("makemytrip", "make my trip")),
    ("redBus", "Travel", ("redbus", "credpayredbus")),
    ("IRCTC", "Travel", ("irctc",)),
    ("Goibibo", "Travel", ("goibibo",)),
    ("Cleartrip", "Travel", ("cleartrip",)),
    ("EaseMyTrip", "Travel", ("easemytrip",)),
    ("Airbnb", "Travel", ("airbnb",)),
    ("Booking.com", "Travel", ("booking com",)),
    ("BookMyShow", "Entertainment", ("bookmyshow", "book my show")),
    ("PVR INOX", "Entertainment", ("pvr", "inox", "pvr inox")),
    ("Cinepolis", "Entertainment", ("cinepolis",)),
    ("Netflix", "Subscriptions", ("netflix",)),
    ("Spotify", "Subscriptions", ("spotify",)),
    ("YouTube Premium", "Subscriptions", ("youtube premium", "youtube music")),
    ("Hotstar", "Subscriptions", ("hotstar", "disney hotstar", "jiohotstar")),
    ("Apple Music", "Subscriptions", ("apple music",)),
    ("Airtel", "Utilities", ("airtel", "bharti airtel")),
    ("Jio", "Utilities", ("reliance jio", "jio recharge", "jio fiber", "jiofiber")),
    ("ACT Fibernet", "Utilities", ("act fibernet", "atria convergence")),
    ("BESCOM", "Utilities", ("bescom",)),
    ("Tata Power", "Utilities", ("tata power",)),
    ("Tata 1mg", "Health", ("tata 1mg", "1mg")),
    ("PharmEasy", "Health", ("pharmeasy",)),
    ("Apollo Pharmacy", "Health", ("apollo pharmacy", "apollo pharmacies", "apollo 24 7")),
    ("Netmeds", "Health", ("netmeds",)),
    ("Practo", "Health", ("practo",)),
    ("Udemy", "Education", ("udemy",)),
    ("Coursera", "Education", ("coursera",)),
    ("80s Malayalees", "Food & dining", ("80 s malayalees", "80s malayalees")),
    ("Bombay Adda", "Food & dining", ("bombay adda",)),
    ("Chandan Spirits", "Food & dining", ("chandan spirits",)),
    ("Coastal Machali", "Food & dining", ("coastal machali",)),
    ("Da Sea Meen", "Food & dining", ("da sea meen",)),
    ("Glacier Dhaba", "Food & dining", ("glacier dhaba",)),
    ("IDC Kitchen", "Food & dining", ("idc kitchen",)),
    ("Kanti Sweets", "Food & dining", ("kanti sweets", "kanti sweets2nd")),
    ("Nagarjuna", "Food & dining", ("nagarjuna andhra",)),
    ("Old Dana Pani", "Food & dining", ("old dana pani",)),
    ("Pind Punjabi", "Food & dining", ("pind punjabi",)),
    ("Pudeena Soda", "Food & dining", ("pudeena soda",)),
    ("Prasad Wine", "Food & dining", ("prasad wine",)),
    ("Riya Foods", "Food & dining", ("riya foods",)),
    ("Rockside Bar", "Food & dining", ("rockside bar",)),
    ("Royal Vintage Bar", "Food & dining", ("royalvintagebarandr",)),
    ("Shetty Lunch Home", "Food & dining", ("shetty lunch home",)),
    ("Sri Siddapaji Tea", "Food & dining", ("sri siddapaji tea",)),
    ("Sri Krishna Bakery", "Food & dining", ("sri krishna bakery",)),
    ("Smoked BBQ", "Food & dining", ("smoked bbq",)),
    ("Toit Brewpub", "Food & dining", ("toit brewpub",)),
    ("Maravanthe", "Food & dining", ("maravanthe",)),
    ("Aditya Celebration Catering", "Food & dining", ("aditya celebration catering",)),
    ("Nandu's", "Groceries", ("nandus rama", "nandus ramamurthyna", "nandus ramamurthyn")),
    ("Fuaark", "Shopping", ("fuaark",)),
    ("The Souled Store", "Shopping", ("the souled store",)),
    ("Sunglass Hut", "Shopping", ("sunglass hut", "sun glass h")),
    ("iPlanet", "Shopping", ("iplanet", "iplanetvrm1")),
    ("BMRCL Metro", "Transport", ("bmrcl",)),
    ("Just Wash Car", "Transport", ("just wash car",)),
    ("ParkPlus", "Transport", ("parkplus",)),
    ("IndiGo", "Travel", ("indigo",)),
    (
        "Kerala State Road Transport",
        "Transport",
        ("kerala state road t", "kerala state road transport"),
    ),
    ("WanderOn", "Travel", ("wanderon", "m s wanderon experi")),
    ("FlightsMojo", "Travel", ("niamy interactive", "flightsmojo")),
    ("Shrreyas Inn", "Travel", ("shrreyas inn",)),
    ("Cineplex", "Entertainment", ("cineplex private",)),
    ("Astrotalk", "Entertainment", ("astrotalk",)),
    ("ChatGPT", "Subscriptions", ("chatgpt",)),
    ("OnlyFans", "Entertainment", ("onlyfans com",)),
    ("Fastcablenet", "Utilities", ("fastcablenet",)),
    ("Udupi Fastnet", "Utilities", ("udupi fastnet",)),
    ("Jio Prepaid", "Utilities", ("jio prepaid",)),
    ("Profile Salon", "Personal care", ("profile salon",)),
    ("HealthKart", "Health", ("bright nutricare", "healthkart")),
    ("Wellbeing Nutrition", "Health", ("nutritionalab", "wellbeing nutrition")),
    ("Optimum Nutrition", "Health", ("optimum nutrition",)),
    ("Sai Radha Pharma", "Health", ("sai radha pharma",)),
    ("V2 Fitness", "Health", ("v2 fitness",)),
    ("New India Assurance", "Insurance", ("the new india assur", "the new india assurance")),
]


def normalized(value):
    text = unicodedata.normalize("NFKC", html.unescape(str(value))).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def business_identity(descriptor):
    """Curated legal-entity evidence; a shortened descriptor remains an inference."""
    text = normalized(descriptor)
    if not re.fullmatch(
        r"(?:upi |pos |ecom )?(?:agione te|agione technologies(?: private limited| pvt ltd)?|emergent sh)(?: icici ban(?:k)?)?",
        text,
    ) or "@" in str(descriptor):
        return None
    return {
        "merchant": "Emergent",
        "category": "Subscriptions",
        "confidence": "medium" if re.search(r"\bagione te\b", text) else "high",
        "explanation": "Emergent lists Agione Technologies Private Limited as its legal entity. The shortened bank descriptor likely refers to Emergent. Subscriptions covers this software service; the descriptor does not establish a recurring plan or the specific purchase.",
        "sources": [
            {
                "title": "Emergent — company and legal information",
                "url": "https://emergent.sh/info",
                "evidence": "Emergent's company page lists Agione Technologies Private Limited under Legal info.",
            }
        ],
    }


def merchant_match(descriptor):
    business = business_identity(descriptor)
    if business:
        return {k: business[k] for k in ("merchant", "category")}
    raw = str(descriptor).strip()
    # A personal UPI handle containing a brand is not evidence of that merchant.
    if "@" in raw:
        local = raw.split("@", 1)[0]
        if not re.fullmatch(r"[a-zA-Z]+", local):
            return None
        raw = local
    text = normalized(raw)
    # Strip known wrappers only. Do not search all tokens in arbitrary names.
    text = re.sub(
        r"^(?:(?:upi|pos|ecom|e com|purchase|payment|payu|pyu|razorpay|raz|rzp|ccavenue|cc avenue|credpay|www)\s+)+",
        "",
        text,
    )
    for merchant, category, aliases in MERCHANTS:
        for alias in aliases:
            if text != alias and not text.startswith(alias + " "):
                continue
            # A short personal name must not be treated as the clothing store.
            if (
                merchant == "Zara"
                and text != "zara"
                and not re.match(r"zara (?:inditex|online|store|retail|india)\b", text)
            ):
                continue
            # Amazon Pay alone also processes bills, wallets and person payments.
            if (
                merchant == "Amazon"
                and text.startswith("amazon pay")
                and not re.search(r"\be commerce\b|\becommerce\b", text)
            ):
                return None
            if merchant == "Swiggy" and re.search(r"\binstamart\b", text):
                return {"merchant": "Swiggy Instamart", "category": "Groceries"}
            return {"merchant": merchant, "category": category}
    return None


ITEM_CATEGORIES = {
    "Groceries": r"\b(?:groceries|rice|lentils|atta|flour|milk|vegetables|fruit|fruits|eggs|cooking oil)\b",
    "Education": r"\b(?:textbook|textbooks|workbook|workbooks|course|courses)\b",
    "Health": r"\b(?:medicine|medicines|prescription|bandages|first aid kit)\b",
    "Shopping": r"\b(?:shirt|shirts|jeans|shoes|dress|dresses|laptop|headphones|phone|smartphone|clothing|electronics|book|books|novel|novels|rice cooker|milk frother|egg boiler)\b",
}


def item_category(evidence):
    # Only explicit item fields in a completed transaction, never footer ads or
    # a broad keyword scan. Mixed or partly unknown baskets remain Shopping.
    lines = re.findall(
        r"(?im)^\s*(?:purchased item|order item|item|product)\s*:\s*([^\n]+)", evidence or ""
    )
    if not lines:
        return None
    categories = set()
    for line in lines:
        matches = {
            category
            for category, pattern in ITEM_CATEGORIES.items()
            if re.search(pattern, line, re.I)
        }
        if len(matches) != 1:
            return None
        categories.update(matches)
    return next(iter(categories)) if len(categories) == 1 else None


def apply_merchant_default(data):
    d = dict(data)
    if d.get("category", "Uncategorized") != "Uncategorized":
        return d
    kind_category = KIND_CATEGORIES.get(d.get("kind"))
    if kind_category:
        d["category"] = kind_category
        d["category_inference"] = {
            "category": kind_category,
            "basis": "transaction_kind",
            "kind": d["kind"],
            "reason": f"Classified as {kind_category.lower()} from the transaction type.",
            "version": VERSION,
        }
        return d
    if d.get("direction") != "debit" or d.get("kind") not in ("purchase", "person_payment"):
        return d
    if str(d.get("counterparty_key") or "").startswith("person:"):
        return d
    abbreviated = re.fullmatch(r"UPI-K-\d{12}-(SWI|ZEP)", str(d.get("counterparty", "")), re.I)
    if abbreviated and str(d.get("account", "")).upper().startswith("KOTAK"):
        code = abbreviated.group(1).upper()
        merchant, category = (
            ("Swiggy", "Food & dining") if code == "SWI" else ("Zepto", "Groceries")
        )
        d["category"] = category
        d["category_inference"] = {
            "merchant": merchant,
            "category": category,
            "basis": "bank_abbreviation",
            "confidence": "inferred",
            "version": VERSION,
            "reason": f"Inferred {merchant} from {code} in the abbreviated Kotak UPI descriptor; item details are unavailable.",
        }
        return d
    match = merchant_match(d.get("counterparty", ""))
    if not match:
        return d
    category = match["category"]
    basis = "merchant"
    reason = f"Recognized {match['merchant']} from the merchant name."
    business = business_identity(d.get("counterparty", ""))
    if business:
        basis, reason = "legal_entity", business["explanation"]
    if match["merchant"] in ("Amazon", "Flipkart"):
        item = item_category(d.get("evidence", ""))
        if item:
            category, basis = item, "item_details"
            reason = f"Recognized {match['merchant']}; explicit purchased-item details indicate {item.lower()}."
        else:
            reason = f"Recognized {match['merchant']}. Shopping is a broad default because the purchased items are unclear or mixed."
    d["category"] = category
    d["category_inference"] = {
        **match,
        "category": category,
        "basis": basis,
        "reason": reason,
        "version": VERSION,
        **(
            {"source_url": business["sources"][0]["url"], "confidence": business["confidence"]}
            if business
            else {}
        ),
    }
    return d
