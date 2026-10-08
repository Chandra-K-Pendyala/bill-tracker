"""Read utility bill PDFs and record them as payments.

Hydro One, Toronto Hydro, Enbridge Gas, Wyse Meter Solutions and North Grenville water have their own
readers, built from real bills. Other major Ontario providers (tracker/providers.py) are read by a general
reader that looks for the usual labels; those records are flagged for review.

Each bill becomes one BillPayment for the month of its statement date, with the PDF attached. A bill also
says what happened to earlier ones (payments received, nothing carried forward, an automatic withdrawal),
so importing it updates the earlier records. Several bills imported together are applied oldest first.
"""
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import Attachment, BillAccount, BillPayment, Property, Provider
from .providers import ONTARIO_PROVIDERS

PDFTOTEXT_TIMEOUT = 30
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_WORDS = r"[A-Z][a-z]+\.? \d{1,2}, \d{4}"          # Jun 21, 2026 · June 1, 2026 · Sept 21, 2026
DATE_DASHED = r"\d{1,2}-[A-Za-z]{3}-\d{4}"            # 02-Jun-2026
DATE_TH = r"[A-Z][a-z]{2} \d{1,2} \d{4}"              # Jul 10 2026
DATE_DMY = r"\d{1,2} [A-Z][a-z]{2} \d{4}"             # 24 Jun 2026
DATE_ANY = (r"(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}[\s-][A-Za-z]{3,9}\.?,?[\s-]\d{4}"
            r"|\d{4}[-/]\d{1,2}[-/]\d{1,2}|[A-Za-z]{3}\s+\d{1,2}/\d{2}(?!\d))")
MONEY = r"[-−]?\$ ?[\d,]+\.\d{2}"                 # $12.34 · −$12.34 · -$5.67
PLAIN_MONEY = r"-?[\d,]+\.\d{2}"
UNIT_WORDS = {"UNIT", "APT", "SUITE", "STE", "BLK", "BLOCK", "LOT", "PH", "FLOOR", "FL", "RM", "ROOM", "NO"}
STREET_TYPES = {
    "AV", "AVE", "AVENUE", "BLVD", "BOULEVARD", "CIR", "CIRCLE", "CLOSE", "COMMON", "CONC", "COURT", "COVE", "CRES",
    "CRESCENT", "CRT", "CT", "DR", "DRIVE", "GATE", "GDNS", "GARDENS", "GROVE", "GRV", "HTS", "HEIGHTS", "HWY", "HIGHWAY",
    "LANE", "LINE", "LN", "MEWS", "PARKWAY", "PATH", "PKWY", "PL", "PLACE", "RD", "ROAD", "ROW", "SQ", "SQUARE", "ST",
    "STREET", "TERR", "TERRACE", "TRAIL", "TRL", "WALK", "WAY",
}
DEDICATED = "Hydro One, Toronto Hydro, Enbridge Gas, Wyse Meter Solutions and North Grenville water"

class BillImportError(Exception):
    """A bill could not be read or recorded. The message is safe to show to the user."""

@dataclass
class ParsedBill:
    provider: str
    category: str
    frequency: str
    account_number: str
    service_address: str
    statement_date: date
    due_date: date
    amount_due: Decimal                 # this bill's own charges; balances carried forward stay on earlier bills
    period_start: date | None = None
    period_end: date | None = None
    usage: Decimal | None = None
    usage_unit: str = ""
    previous_settled: bool = False      # nothing was carried forward, so every earlier bill is paid
    payments: list[tuple[date, Decimal]] = field(default_factory=list)  # received toward earlier bills
    carried: Decimal = Decimal("0")     # unpaid balance from earlier bills included in what this bill asks for
    amount_to_pay: Decimal | None = None  # what the bill asks for, after earlier balances and credits
    autopay_date: date | None = None    # the provider withdraws the amount on this date
    needs_review: bool = False          # read by the general reader
    provider_detected: bool = False     # the company name was read off the bill, not from the known list
    note: str = ""

    @property
    def label(self): return f"{self.provider} · {self.statement_date:%b %Y}"

@dataclass
class ImportResult:
    filename: str
    outcome: str                        # created · updated · duplicate · error
    message: str
    payment: BillPayment | None = None
    settled: list[BillPayment] = field(default_factory=list)

def _money(value):
    value = value.replace("−", "-").replace("$", "").replace(",", "").replace(" ", "")
    return -Decimal(value[:-2]) if value.upper().endswith("CR") else Decimal(value)

def _date(value):
    """Parse 'Jun 21, 2026', 'JUNE 1 2026', 'Sept 21, 2026', '24 Jun 2026', '02-Jun-2026', 'May 31/26' or '2026-06-21'."""
    value = re.sub(r"\s+", " ", value.strip())
    if m := re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value): return date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}),? (\d{4})", value): month, day, year = m[1], m[2], m[3]
    elif m := re.fullmatch(r"(\d{1,2})[ -]([A-Za-z]+)\.?,?[ -](\d{4})", value): day, month, year = m[1], m[2], m[3]
    elif m := re.fullmatch(r"([A-Za-z]+) (\d{1,2})/(\d{2})", value): month, day, year = m[1], m[2], "20" + m[3]
    else: raise ValueError(value)
    return date(int(year), MONTHS[month[:3].lower()], int(day))

def _find(pattern, text, what, provider, flags=0):
    if m := re.search(pattern, text, flags): return m
    raise BillImportError(f"Couldn't find the {what} on this {provider} bill.")

def _period(match):
    return (_date(match[1]), _date(match[2])) if match else (None, None)

def _money_label(value): return f"${value:,.2f}"

def _date_label(value): return f"{value:%b} {value.day}, {value.year}"

def _carried_note(total, carried):
    if carried > 0: return f"The bill asks for {_money_label(total)}, including {_money_label(carried)} carried from earlier bills."
    if carried < 0: return f"A credit of {_money_label(-carried)} from earlier bills brings the amount to pay to {_money_label(max(total, Decimal(0)))}."
    return ""

def _parse_hydro_one(text):
    name = "Hydro One"
    total = _money(_find(rf"Total amount you owe\s+({MONEY})", text, "amount owing", name)[1])
    carried = re.search(rf"Balance carried forward from previous statement\s+({MONEY})", text)
    carried = _money(carried[1]) if carried else Decimal("0")
    start, end = _period(re.search(rf"For the period of:\s+({DATE_WORDS})\s*-\s*({DATE_WORDS})", text))
    usage = [_money(u) for u in re.findall(r"\(x[\d.]+\)\s*=\s*([\d,]+(?:\.\d+)?)", text)]
    return ParsedBill(
        provider=name, category="electricity", frequency="monthly",
        account_number=_find(r"Your account number is:\s+(\d[\d ]*\d)", text, "account number", name)[1],
        service_address=_find(r"Powering\s+(.+?)(?:\s{2,}|$)", text, "service address", name, re.M)[1],
        statement_date=_date(_find(rf"This statement is issued on:\s+({DATE_WORDS})", text, "statement date", name)[1]),
        due_date=_date(_find(rf"If payment is not received by\s+({DATE_WORDS})", text, "due date", name)[1]),
        amount_due=total - carried if carried > 0 else total, period_start=start, period_end=end,
        usage=sum(usage) if usage else None, usage_unit="kWh" if usage else "", previous_settled=carried <= 0,
        payments=[(_date(d), abs(_money(a))) for d, a in
                  re.findall(rf"Amount we received on\s+([A-Za-z]+ \d{{1,2}}/\d{{2}})\s+({MONEY})", text)],
        carried=carried, amount_to_pay=total, note=_carried_note(total, carried),
    )

def _parse_toronto_hydro(text):
    name = "Toronto Hydro"
    total = _money(_find(rf"Amount Due\s+({MONEY})", text, "amount due", name)[1])
    forward = re.search(rf"Balance Forward\s+({PLAIN_MONEY}(?: CR)?)", text)
    forward = _money(forward[1]) if forward else Decimal("0")
    meters = re.findall(r"([A-Z]{3} \d{2} \d{4}) TO ([A-Z]{3} \d{2} \d{4})\s+\d+\s+\S+\s+[\d.,]+\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)", text)
    withdrawal = re.search(rf"Amount to be Withdrawn\s+({DATE_TH})", text)
    return ParsedBill(
        provider=name, category="electricity", frequency="monthly",
        account_number=_find(r"Account Number\s+Premises Number[^\n]*\n\s*(\d[\d ]*?\d)(?:\s{2,}|\s*$)", text, "account number", name, re.M)[1],
        service_address=_find(r"Service Location:\s*(.+?)(?:\s{2,}|$)", text, "service address", name, re.M)[1],
        statement_date=_date(_find(rf"Statement Date\s+({DATE_TH})", text, "statement date", name)[1]),
        due_date=_date(_find(rf"Due Date:?\s+({DATE_TH})", text, "due date", name)[1]),
        amount_due=total - forward if forward > 0 else total,
        period_start=min(_date(s) for s, _, _ in meters) if meters else None,
        period_end=max(_date(e) for _, e, _ in meters) if meters else None,
        usage=sum(_money(u) for *_, u in meters) if meters else None, usage_unit="kWh" if meters else "",
        previous_settled=forward <= 0,
        payments=[(_date(d), _money(a)) for d, a in re.findall(rf"Payment Received ({DATE_TH})[^\n]*?\s({PLAIN_MONEY}) CR", text)],
        carried=forward, amount_to_pay=total, autopay_date=_date(withdrawal[1]) if withdrawal else None,
        note=_carried_note(total, forward),
    )

def _parse_enbridge(text):
    name = "Enbridge Gas"
    header = _find(rf"Account Number\s+Bill Date[ \t]*\n\s*(\d[\d ]*\d)\s{{2,}}({DATE_WORDS})", text,
                   "account number and bill date", name)
    total = _money(_find(rf"Total Amount Due\s+({MONEY})", text, "amount due", name)[1])
    forward = re.search(rf"Balance Forward\s+({MONEY})", text)
    forward = _money(forward[1]) if forward else Decimal("0")
    address_line = _find(r"Service Address[ \t]*\n([^\n]+)", text, "service address", name)[1]
    start, end = _period(re.search(rf"Billing Period\s+({DATE_WORDS})\s*-\s*({DATE_WORDS})", text))
    used_at = text.find("You used")
    usage = re.search(r"(?<![\d.])(\d+(?:\.\d+)?) ?m³(?!\s*per day)", text[used_at:]) if used_at >= 0 else None
    return ParsedBill(
        provider=name, category="natural_gas", frequency="monthly",
        account_number=header[1], service_address=re.split(r"\s{2,}", address_line.strip())[-1],
        statement_date=_date(header[2]),
        due_date=_date(_find(rf"Due Date[^\n]*\n(?:[^\n]*\n){{0,3}}?[ \t]*({DATE_WORDS})", text, "due date", name)[1]),
        amount_due=total - forward if forward > 0 else total, period_start=start, period_end=end,
        usage=Decimal(usage[1]) if usage else None, usage_unit="m³" if usage else "", previous_settled=forward <= 0,
        payments=[(_date(d), abs(_money(a))) for d, a in re.findall(rf"Payment Received\s*\[({DATE_WORDS})\]\s+({MONEY})", text)],
        carried=forward, amount_to_pay=total, note=_carried_note(total, forward),
    )

def _parse_wyse(text):
    name = "Wyse Meter Solutions"
    due = _find(rf"Amount Due On ([A-Z][a-z]{{2}} \d{{1,2}}/\d{{2}})\s+({MONEY})", text, "due date and amount", name)
    to_pay = _money(due[2])
    forward = _money(_find(rf"Balance Forward\s+({MONEY})", text, "balance forward", name)[1])
    meters = re.findall(rf"(Water|Electricity|Gas|Thermal)\s+(\S+)\s+({DATE_DMY}):\s*[\d,.]+\s+({DATE_DMY}):\s*[\d,.]+\s+([\d,.]+)", text)
    kinds, units = {m[0] for m in meters}, {m[1].upper() for m in meters}
    one_kind = len(kinds) == 1 and len(units) == 1
    return ParsedBill(
        provider=name, frequency="monthly",
        category={"Water": "water", "Electricity": "electricity", "Gas": "natural_gas"}.get(kinds.pop(), "other") if one_kind else "other",
        account_number=_find(r"Your Account Number:\s*([\w-]+)", text, "account number", name)[1],
        service_address=_find(r"Service Address:\s*(.+?)(?:\s{2,}|$)", text, "service address", name, re.M)[1],
        statement_date=_date(_find(rf"Issued On:\s*({DATE_WORDS})", text, "statement date", name)[1]),
        due_date=_date(due[1]),
        amount_due=_money(_find(rf"Your Total Charges for [^\n$]*?({MONEY})", text, "total charges", name)[1]),
        period_start=min(_date(m[2]) for m in meters) if meters else None,
        period_end=max(_date(m[3]) for m in meters) if meters else None,
        usage=sum(_money(m[4]) for m in meters) if meters and one_kind else None,
        usage_unit={"GAL": "gal", "KWH": "kWh", "M3": "m³", "M³": "m³"}.get(units.pop(), "") if meters and one_kind else "",
        previous_settled=forward <= 0, carried=forward, amount_to_pay=to_pay, note=_carried_note(to_pay, forward),
    )

def _parse_north_grenville(text):
    name = "North Grenville water"
    levy = _money(_find(rf"Current Levy\s+({PLAIN_MONEY})", text, "current charges", name)[1])
    balance = _money(_find(rf"Account Balance Due By [^$\n]*\$\s*({PLAIN_MONEY})", text, "balance due", name)[1])
    start, end = re.search(rf"Bill From:\s*({DATE_DASHED})", text), re.search(rf"Bill To:\s*({DATE_DASHED})", text)
    usage = [_money(u) for u in re.findall(r"Consumption:\s*([\d,]*\.?\d+)\s*CU METERS", text)]
    return ParsedBill(
        provider="Municipality of North Grenville", category="water", frequency="bi_monthly",
        account_number=_find(r"Account #:\s*(\d[\d ]*\d)", text, "account number", name)[1],
        service_address=_find(r"Service Address:\s*(.+?)(?:\s{2,}|$)", text, "service address", name, re.M)[1],
        statement_date=_date(_find(rf"Billing Date:\s*({DATE_DASHED})", text, "billing date", name)[1]),
        due_date=_date(_find(rf"Due Date\s*:\s*({DATE_DASHED})", text, "due date", name)[1]),
        amount_due=min(levy, balance), period_start=_date(start[1]) if start else None, period_end=_date(end[1]) if end else None,
        usage=sum(usage) if usage else None, usage_unit="m³" if usage else "", previous_settled=balance <= levy,
        carried=balance - levy, amount_to_pay=balance, note=_carried_note(balance, balance - levy),
    )

# The general reader: labels most Canadian bills use, searched case-insensitively. A value is taken from
# the rest of the label's line, or failing that from the next two lines (two-column layouts).
AMOUNT_LABELS = (r"total\s+amount\s+(?:now\s+)?(?:due|owing|payable)|amount\s+(?:now\s+)?due|balance\s+(?:now\s+)?due"
                 r"|total\s+(?:due|payable)|amount\s+owing|(?:total\s+)?amount\s+you\s+owe|new\s+balance|please\s+pay"
                 r"|pay\s+this\s+amount")
DUE_LABELS = (r"(?:payment\s+)?due\s+date|payment\s+due(?:\s+(?:by|on))?|due\s+(?:by|on)|(?:please\s+)?pay\s+by"
              r"|(?:payment\s+is\s+)?not\s+received\s+by")
STATEMENT_LABELS = (r"statement\s+date|bill(?:ing)?\s+date|date\s+of\s+(?:bill|statement|invoice)|issued?\s+(?:date|on)"
                    r"|date\s+issued|invoice\s+date")
ACCOUNT_LABELS = r"account\s*(?:number|no\.?|#)|customer\s*(?:number|no\.?|#)|acct\.?\s*(?:number|no\.?|#)"
ADDRESS_LABELS = r"service\s+(?:address|location)|premises\s+address|property\s+address|supply\s+address|powering"
PERIOD_LABELS = r"(?:billing|service|bill|statement)\s+period|for\s+the\s+period(?:\s+of)?|period\s+(?:of|from)"
GENERAL_MONEY = r"-?\$\s?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)(?:\s?CR\b)?|(?<![\w.$])\d{1,3}(?:,\d{3})*\.\d{2}(?![\d%])(?:\s?CR\b)?"
ACCOUNT_VALUE = r"(?<![\w-])[A-Z]{0,3}-?\d(?:[\d-]| (?=\d)){3,}[\dA-Z]"

def _labelled(text, labels, value, next_lines=2, accept=lambda segment, match: True):
    """The value for the first label that has one: the first value after it on its own line, or else the value
    on one of the next lines that sits closest to the label's column (labels above values in columns)."""
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]: starts.append(starts[-1] + len(line) + 1)
    def candidates(segment):
        return [m for m in re.finditer(value, segment, re.I) if accept(segment, m)]
    for label in re.finditer(labels, text, re.I):
        row = next(i for i in range(len(starts) - 1, -1, -1) if starts[i] <= label.start())
        column = label.start() - starts[row]
        if found := candidates(lines[row][label.end() - starts[row]:]): return found[0]
        for line in lines[row + 1:row + 1 + next_lines]:
            if found := candidates(line): return min(found, key=lambda m: abs(m.start() - column))
    return None

MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"

def _looks_like_account(segment, match):
    """At least five digits, and not a date: neither '2026-09-01' nor the day and year of 'Jul 10 2026'."""
    before = segment[max(0, match.start() - 10):match.start()]
    return (len(re.sub(r"\D", "", match[0])) >= 5 and not re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", match[0])
            and not re.search(rf"\b{MONTH_WORD}\s*$", before, re.I))

def recognize_provider(text):
    """The provider (name, category) whose name appears earliest in the bill, or None."""
    found = [(m.start(), name, category) for name, category, _, pattern in ONTARIO_PROVIDERS
             if (m := re.search(pattern, text, re.I))]
    return min(found)[1:] if found else None

# Reading the company's name off bills from providers that aren't in tracker/providers.py. Candidates come from
# the payee line, government names, business-style names (ending in Inc., Utilities, Hydro, Co-op…) and are
# confirmed by the bill's web address; the best-scoring one wins.
ORG_ENDINGS = {"INC", "LTD", "LIMITED", "CORP", "CORPORATION", "LLC", "LLP", "CO-OP", "COOP", "COOPERATIVE", "COMPANY",
               "UTILITIES", "UTILITY", "HYDRO", "ENERGY", "POWER", "GAS", "WATER", "ELECTRIC", "TELECOM", "COMMUNICATIONS",
               "WIRELESS", "MOBILITY", "MOBILE", "NETWORKS", "SERVICES", "SOLUTIONS", "INSURANCE", "PROPANE", "FUELS",
               "OIL", "METERING", "INTERNET", "CABLE", "TELEPHONE", "GROUP"}
GENERIC_WORDS = {"YOUR", "OUR", "THE", "TOTAL", "NATURAL", "SUPPLY", "DELIVERY", "SERVICE", "CUSTOMER", "ACCOUNT", "BILL",
                 "BILLING", "PAYMENT", "CHARGE", "CHARGES", "AMOUNT", "NEW", "CURRENT", "MONTHLY", "ONTARIO", "ELECTRICITY",
                 "AND", "OF", "FOR", "MY", "PLEASE", "PAY", "INFORMATION", "USAGE", "RATE", "RATES", "HOME", "RESIDENTIAL"}
NOT_THE_BILLER = re.compile(
    r"ontario\s+energy\s+board|independent\s+electricity|\bieso\b|electricity\s+support\s+program|canada\s+revenue"
    r"|canada\s+post|government\s+of|province\s+of|ontario\s+electricity\s+rebate|financial\s+institution|royal\s+bank"
    r"|\brbc\b|td\s+canada|scotiabank|bank\s+of\s+montreal|\bbmo\b|\bcibc\b|tangerine|desjardins|interac|\bvisa\b"
    r"|mastercard|american\s+express|paymentus|moneris", re.I)
GOVERNMENT = (r"(?i:regional municipality|municipality|city|town|township|village|county|region) (?i:of) "
              r"[A-Z][\w'.-]*(?: [A-Z][\w'.-]*){0,3}")  # the place name itself must be capitalized
PAYEE = (r"(?:cheques?\s+(?:are\s+)?payable\s+to|payable\s+to|remit(?:\s+payments?)?\s+to|make\s+(?:your\s+)?payments?\s+to"
         r"|pay\s+to\s+the\s+order\s+of)\s*:?\s*")
NAME_RUN = r"[A-Z][\w&'.-]*(?: [A-Z&][\w&'.-]*){0,5}"  # capitalized words separated by single spaces
WEB_DOMAIN = r"\b(?:www\.)?([a-z0-9-]{4,})\.(?:com|ca|net|org)\b"
SMALL_WORDS = {"of", "and", "the", "de", "du", "la", "des"}
CATEGORY_HINTS = [
    ("electricity", r"\bkwh\b|electricity|\bhydro\b|\bpower\b"), ("natural_gas", r"natural\s+gas|gas\s+supply|\bgas\b"),
    ("water", r"\bwater\b|sewer|wastewater"), ("mobile", r"wireless|\bmobile\b|mobility|cell\s*phone|data\s+plan"),
    ("internet", r"internet|broadband|wi-?fi|modem|telecom"), ("tv", r"television|\bcable\b|\btv\b"),
    ("insurance", r"insurance|premium|policy\s+(?:number|no|#)"), ("property_tax", r"property\s+tax|tax\s+levy|assessment\s+roll"),
    ("condo_fees", r"condo(?:minium)?\s+fees?|maintenance\s+fees?|common\s+expenses"), ("mortgage_rent", r"\brent\b|\blease\b|mortgage"),
    ("credit_card", r"credit\s+card|minimum\s+payment"), ("loan", r"\bloan\b|amortization"),
    ("subscription", r"subscription|membership"),
]

def _clean_name(name):
    """'HYDRO EXAMPLE NETWORKS INC.' -> 'Hydro Example Networks': trimmed, legal suffix dropped, capitals tidied."""
    name = re.sub(r"\s+", " ", name).strip(" ,;:.")
    name = re.sub(r"(?:,?\s+(?:inc|ltd|limited|corp|corporation|co|company|llc|llp)\.?)+$", "", name, flags=re.I)
    if name.isupper():
        words = name.split(" ")
        name = " ".join(w.lower() if i and w.lower() in SMALL_WORDS else ("Co-op" if w == "CO-OP" else w.capitalize())
                        for i, w in enumerate(words))
    return name

def _distinctive(name):
    """True when a name has a word of its own, so 'Natural Gas' or 'Your Water' don't count as companies."""
    words = re.findall(r"[A-Za-z][A-Za-z'&-]*", name.upper())
    return any(w not in ORG_ENDINGS and w not in GENERIC_WORDS and len(w) > 1 for w in words)

def _guess_category(text, name):
    """Strongest evidence first: words in the company's name, then the units billed, then (weakly) the wording."""
    score = {category: 3 * len(re.findall(pattern, name, re.I)) + len(re.findall(pattern, text, re.I)) / 10
             for category, pattern in CATEGORY_HINTS}
    if re.search(r"\d\s*kwh\b", text, re.I): score["electricity"] += 4
    if re.search(r"\d\s*(?:gal|gallons)\b", text, re.I): score["water"] += 4
    if re.search(r"\d\s*(?:m³|m3|cu\.?\s*met(?:er|re)s?)", text, re.I):
        score["natural_gas" if re.search(r"natural\s+gas|gas\s+supply", text, re.I) else "water"] += 3
    category, best = max(score.items(), key=lambda item: item[1])
    return category if best >= 1 else "other"

def detect_provider(text):
    """The company that issued a bill, read from its text: (name, category), or None when nothing is convincing."""
    scores = {}
    def add(raw, points):
        name = _clean_name(raw)
        if len(name) < 3 or NOT_THE_BILLER.search(name) or not _distinctive(name): return
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        shown, total = scores.get(key, (name, 0))
        scores[key] = (shown, total + points)
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]: starts.append(starts[-1] + len(line) + 1)
    for payee in re.finditer(PAYEE, text, re.I):                      # "Make cheques payable to: …"
        row = max(i for i, start in enumerate(starts) if start <= payee.start())
        after = text[payee.end():starts[row] + len(lines[row])].strip()
        if after and not re.match(r"(?:p\.?\s*o\.?\s*box|\d)", after, re.I):
            if run := re.match(NAME_RUN, re.split(r"\s{2,}", after)[0]): add(run[0], 5)
            continue
        column = payee.start() - starts[row]                         # name printed under the label instead
        for line in lines[row + 1:row + 3]:
            pieces = [(m.start(), m[0]) for m in re.finditer(r"\S+(?: \S+)*", line)]
            pieces = [(abs(col - column), piece) for col, piece in pieces if not re.match(r"(?:p\.?\s*o\.?\s*box|\d)", piece, re.I)]
            if pieces and (run := re.match(NAME_RUN, min(pieces)[1])):
                add(run[0], 5); break
    for gov in re.finditer(GOVERNMENT, text): add(gov[0], 4)  # "City of …", "Municipality of …"
    for row, line in enumerate(lines):                               # names ending like a company
        for run in re.finditer(NAME_RUN, line):
            words = run[0].replace(".", "").upper().split(" ")
            while words and words[-1] not in ORG_ENDINGS: words.pop()
            if len(words) >= 2: add(" ".join(run[0].split(" ")[:len(words)]), 3 if row < 15 else 1)
    domains = {d.replace("-", "") for d in re.findall(WEB_DOMAIN, text.lower())}
    for key, (name, total) in list(scores.items()):
        if any(d in key or key in d for d in domains if len(d) >= 5): scores[key] = (name, total + 3)
    if not scores: return None
    name, total = max(scores.values(), key=lambda item: item[1])
    return (name, _guess_category(text, name)) if total >= 3 else None

def _parse_general(text, provider, category, detected=False):
    amount = _labelled(text, AMOUNT_LABELS, GENERAL_MONEY)
    if amount is None: raise BillImportError(f"Couldn't find the amount due on this {provider} bill.")
    due = _labelled(text, DUE_LABELS, DATE_ANY)
    statement = _labelled(text, STATEMENT_LABELS, DATE_ANY)
    if due is None and statement is None: raise BillImportError(f"Couldn't find the bill date or due date on this {provider} bill.")
    account = _labelled(text, ACCOUNT_LABELS, ACCOUNT_VALUE, accept=_looks_like_account)
    if account is None: raise BillImportError(f"Couldn't find the account number on this {provider} bill.")
    address = re.search(rf"(?:{ADDRESS_LABELS})\s*:?\s*(.+?)(?:\s{{2,}}|$)", text, re.I | re.M)
    period = _labelled(text, PERIOD_LABELS, rf"({DATE_ANY})\s*(?:-|–|to|through)\s*({DATE_ANY})", next_lines=1)
    statement_date = _date(statement[0]) if statement else _date(due[0])
    total = _money(amount[0])
    notes = ["Read by the general bill reader: check the amount and dates."]
    if detected: notes.append(f"The company name, {provider}, was read from the bill; rename it under Providers if it's wrong.")
    if statement is None: notes.append("The bill date wasn't found, so the due date is used.")
    return ParsedBill(
        provider=provider, category=category, frequency="monthly", account_number=account[0].strip(),
        service_address=address[1].strip() if address else "", statement_date=statement_date,
        due_date=_date(due[0]) if due else statement_date, amount_due=total, amount_to_pay=total,
        period_start=_date(period[1]) if period else None, period_end=_date(period[2]) if period else None,
        needs_review=True, provider_detected=detected, note=" ".join(notes),
    )

def parse_bill(text, fallback=None):
    """Turn the text of a bill into a ParsedBill, or raise BillImportError.

    fallback is an optional (provider name, category) for bills whose provider isn't recognized."""
    lower = text.lower()
    if "municipality of north grenville" in lower: parser = _parse_north_grenville
    elif "torontohydro.com" in lower and "Statement Date" in text: parser = _parse_toronto_hydro
    elif "Hydro One" in text and "Your account number is" in text: parser = _parse_hydro_one
    elif "enbridgegas.com" in lower: parser = _parse_enbridge
    elif "wysemeter" in lower: parser = _parse_wyse
    elif known := recognize_provider(text) or fallback: parser = lambda t: _parse_general(t, *known)
    elif detected := detect_provider(text): parser = lambda t: _parse_general(t, *detected, detected=True)
    else:
        raise BillImportError("The company's name couldn't be found on this bill. Choose the provider under “Read "
                              "unrecognized bills as” and upload it again, or add the bill by hand.")
    try: return parser(text)
    except (ValueError, KeyError, InvalidOperation) as exc:
        raise BillImportError("A date or amount on this bill couldn't be read.") from exc

def extract_text(f):
    """Return the text layer of a PDF (an uploaded or opened Django File) using poppler's pdftotext."""
    f.seek(0)
    if f.read(5) != b"%PDF-": raise BillImportError("This file isn't a PDF.")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        for chunk in f.chunks(): tmp.write(chunk)
        tmp.flush()
        try:
            result = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-l", "10", tmp.name, "-"],
                                    capture_output=True, timeout=PDFTOTEXT_TIMEOUT, check=False)
        except FileNotFoundError as exc: raise BillImportError("pdftotext (poppler-utils) isn't installed on the server.") from exc
        except subprocess.TimeoutExpired as exc: raise BillImportError("Reading this PDF took too long.") from exc
    f.seek(0)
    if result.returncode != 0: raise BillImportError("This PDF couldn't be read.")
    text = result.stdout.decode("utf-8", "replace")
    if not text.strip(): raise BillImportError("This PDF has no text to read. Is it a scan?")
    return text

def street_key(address):
    """Civic number and street name: ('52', 'DEGRASSI') for '52 De Grassi St, Unit 1' or 'UNIT 1-52 DE GRASSI ST'."""
    tokens = re.findall(r"[A-Z0-9]+", address.upper())
    for i, token in enumerate(tokens):
        if not re.fullmatch(r"\d+[A-Z]?", token): continue
        words = []
        for word in tokens[i + 1:]:
            if re.fullmatch(r"\d+[A-Z]?", word) or word in UNIT_WORDS or (words and word in STREET_TYPES): break
            words.append(word)
        if words: return token, "".join(words)
    return None

def match_property(user, address):
    key = street_key(address)
    matches = [p for p in Property.objects.filter(owner=user) if key and street_key(p.street_address) == key]
    return matches[0] if len(matches) == 1 else None

def _digits(value): return re.sub(r"\D", "", value)

def _provider_for(user, bill):
    """The built-in or custom provider with the bill's provider name, creating a custom one if needed."""
    existing = (Provider.objects.filter(owner__isnull=True, name=bill.provider).first()
                or Provider.objects.filter(owner=user, name__iexact=bill.provider).first())
    if existing: return existing, False
    return Provider.objects.create(owner=user, name=bill.provider, category=bill.category, province_region="ON"), True

def _account_for(user, bill, property):
    """(account, new account, new provider). A company read off the bill is matched by account number first, so
    later bills still find the account after its provider is renamed."""
    if bill.provider_detected:
        for account in BillAccount.objects.filter(owner=user).select_related("provider"):
            if _digits(account.account_number) == _digits(bill.account_number): return account, False, False
    provider, new_provider = _provider_for(user, bill)
    for account in BillAccount.objects.filter(owner=user, provider=provider):
        if _digits(account.account_number) == _digits(bill.account_number): return account, False, new_provider
    prop = match_property(user, bill.service_address) or property  # the chosen property is only a fallback
    if prop is None:
        raise BillImportError(f"{bill.provider} account {bill.account_number} isn't in the tracker yet, and its service address "
                              f"({bill.service_address or 'not found on the bill'}) doesn't match exactly one of your properties. "
                              f"Add the property, or choose it when you upload.")
    account = BillAccount(owner=user, provider=provider, property=prop, account_number=bill.account_number,
                          billing_frequency=bill.frequency, typical_amount=bill.amount_due, due_day=bill.due_date.day)
    try: account.full_clean()
    except ValidationError as exc: raise BillImportError(f"The new {bill.provider} account couldn't be saved: {exc.messages[0]}") from exc
    account.save()
    return account, True, new_provider

PAYMENT_FIELDS = ["amount_paid", "payment_date", "payment_method", "updated_at"]

def _settle_earlier(account, bill):
    """Record what this bill says about earlier ones; returns the records it marked paid.

    Each payment the bill lists goes to the earlier bill with exactly that amount (correcting the date of one
    that was only expected to be withdrawn); payments that match no bill go to the latest unpaid one. If
    nothing was carried forward, every earlier bill is paid."""
    current = (bill.statement_date.year, bill.statement_date.month)
    earlier = sorted((p for p in account.payments.all() if (p.billing_year, p.billing_month) < current),
                     key=lambda p: (p.billing_year, p.billing_month), reverse=True)
    settled, matched, unmatched = [], [], []
    for paid_on, amount in bill.payments:
        match = next((p for p in earlier if p.amount_due == amount and p not in matched
                      and (p.amount_paid < p.amount_due or p.payment_method == "auto_pay")), None)
        if match is None:
            unmatched.append((paid_on, amount)); continue
        matched.append(match)
        if match.amount_paid < match.amount_due: settled.append(match)
        if match.payment_method == "auto_pay" and match.payment_date and paid_on < match.payment_date:
            match.payment_method = ""  # paid before the scheduled withdrawal
        match.amount_paid, match.payment_date = match.amount_due, paid_on
        match.save(update_fields=PAYMENT_FIELDS)
    unpaid = [p for p in earlier if p.amount_paid < p.amount_due]
    if unmatched and unpaid:
        latest = unpaid[0]
        latest.amount_paid = min(latest.amount_due, max(latest.amount_paid, sum(amount for _, amount in unmatched)))
        latest.payment_date = latest.payment_date or max(d for d, _ in unmatched)
        latest.save(update_fields=PAYMENT_FIELDS)
        if latest.amount_paid >= latest.amount_due: settled.append(latest)
    if bill.previous_settled:
        for payment in unpaid:
            if payment.amount_paid < payment.amount_due:
                payment.amount_paid = payment.amount_due
                payment.save(update_fields=PAYMENT_FIELDS)
                if payment not in settled: settled.append(payment)
    return settled

def _schedule_autopay(account, payment, bill):
    """The provider withdraws what this bill asks for on the autopay date: this bill and any balance carried into it."""
    scheduled = []
    if bill.carried > 0:
        current = (bill.statement_date.year, bill.statement_date.month)
        for earlier in account.payments.filter(amount_paid__lt=F("amount_due")):
            if (earlier.billing_year, earlier.billing_month) < current: scheduled.append(earlier)
    for record in [payment, *scheduled]:
        record.amount_paid = record.amount_due
        if record is payment or not record.payment_date: record.payment_date = bill.autopay_date
        record.payment_method = record.payment_method or "auto_pay"
        record.save(update_fields=PAYMENT_FIELDS)
    if not account.auto_pay:
        account.auto_pay = True
        account.save(update_fields=["auto_pay", "updated_at"])
    return scheduled

def _import_one(user, bill, f, filename, property):
    account, new_account, new_provider = _account_for(user, bill, property)
    label = f"{account.provider.name} · {bill.statement_date:%b %Y}"
    year, month = bill.statement_date.year, bill.statement_date.month
    if duplicate := account.payments.filter(statement_date=bill.statement_date).first():
        return ImportResult(filename, "duplicate", f"{label}: already imported.", duplicate)
    payment = account.payments.filter(billing_year=year, billing_month=month).first()
    if payment and payment.statement_date:
        raise BillImportError(f"{label}: a different bill dated {_date_label(payment.statement_date)} is already recorded "
                              f"for that month. Edit that record by hand.")
    outcome = "updated" if payment else "created"
    payment = payment or BillPayment(owner=user, bill_account=account, billing_year=year, billing_month=month)
    for name in ("statement_date", "due_date", "amount_due", "period_start", "period_end", "usage", "usage_unit", "needs_review"):
        setattr(payment, name, getattr(bill, name))
    if bill.note and bill.note not in payment.notes: payment.notes = f"{payment.notes}\n{bill.note}".strip()
    covered = bill.amount_to_pay is not None and bill.amount_to_pay <= 0 < bill.amount_due
    if covered: payment.amount_paid = max(payment.amount_paid, bill.amount_due)
    payment.save()
    Attachment.objects.create(payment=payment, file=f, original_name=filename[:255])
    settled = _settle_earlier(account, bill)
    scheduled = _schedule_autopay(account, payment, bill) if bill.autopay_date else []
    message = f"{label}: {_money_label(bill.amount_due)} due {_date_label(bill.due_date)}"
    if new_provider: message += f" (new provider, read from the bill)"
    if new_account: message += f" (new account at {account.property})"
    if outcome == "updated": message += " (filled in the existing record)"
    if covered: message += ", covered by a credit on the account"
    if bill.autopay_date: message += f", withdrawn automatically on {_date_label(bill.autopay_date)}"
    if settled: message += "; marked " + ", ".join(p.period_label for p in settled) + " paid"
    if scheduled: message += "; " + ", ".join(p.period_label for p in scheduled) + " carried into the withdrawal"
    if bill.needs_review: message += ". Read by the general reader, so please check it"
    return ImportResult(filename, outcome, message + ".", payment, settled)

def import_bills(user, files, property=None, provider=None):
    """Import (file, filename) pairs for user. Unreadable files are reported and skipped; the rest go in oldest first.

    provider (a Provider) is used for bills whose provider isn't recognized; they are read by the general reader."""
    fallback = (provider.name, provider.category) if provider else None
    results, parsed = [], []
    for f, filename in files:
        try: parsed.append((parse_bill(extract_text(f), fallback), f, filename))
        except BillImportError as exc: results.append(ImportResult(filename, "error", f"{filename}: {exc}"))
    for bill, f, filename in sorted(parsed, key=lambda item: item[0].statement_date):
        try:
            with transaction.atomic(): results.append(_import_one(user, bill, f, filename, property))
        except BillImportError as exc: results.append(ImportResult(filename, "error", f"{filename}: {exc}"))
    return results
