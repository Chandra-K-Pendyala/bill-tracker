"""Read utility bill PDFs (Hydro One, Enbridge Gas, North Grenville water) and record them as payments.

Each bill becomes one BillPayment for the month of its statement date, with the PDF attached. A bill
also says what happened to earlier bills (payments received, nothing carried forward), so importing it
marks the earlier records paid. Import several bills at once and they are applied oldest first.
"""
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Attachment, BillAccount, BillPayment, Property, Provider

PDFTOTEXT_TIMEOUT = 30
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_WORDS = r"[A-Z][a-z]+\.? \d{1,2}, \d{4}"          # Jun 21, 2026 · June 1, 2026 · Sept 21, 2026
DATE_DASHED = r"\d{1,2}-[A-Za-z]{3}-\d{4}"            # 02-Jun-2026
MONEY = r"[-−]?\$ ?[\d,]+\.\d{2}"                 # $12.34 · −$12.34 · -$5.67
PLAIN_MONEY = r"-?[\d,]+\.\d{2}"
UNIT_WORDS = {"UNIT", "APT", "SUITE", "STE", "BLK", "BLOCK", "LOT", "PH", "FLOOR", "FL", "RM", "ROOM", "NO"}
STREET_TYPES = {
    "AV", "AVE", "AVENUE", "BLVD", "BOULEVARD", "CIR", "CIRCLE", "CLOSE", "COMMON", "CONC", "COURT", "COVE", "CRES",
    "CRESCENT", "CRT", "CT", "DR", "DRIVE", "GATE", "GDNS", "GARDENS", "GROVE", "GRV", "HTS", "HEIGHTS", "HWY", "HIGHWAY",
    "LANE", "LINE", "LN", "MEWS", "PARKWAY", "PATH", "PKWY", "PL", "PLACE", "RD", "ROAD", "ROW", "SQ", "SQUARE", "ST",
    "STREET", "TERR", "TERRACE", "TRAIL", "TRL", "WALK", "WAY",
}

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
    amount_due: Decimal                 # what this bill asks for; balances carried forward stay on earlier bills
    period_start: date | None = None
    period_end: date | None = None
    usage: Decimal | None = None
    usage_unit: str = ""
    previous_settled: bool = False      # nothing was carried forward, so every earlier bill is paid
    payments: list[tuple[date, Decimal]] = field(default_factory=list)  # received toward earlier bills

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
    return Decimal(value.replace("−", "-").replace("$", "").replace(",", "").replace(" ", ""))

def _date(value):
    """Parse 'Jun 21, 2026', 'June 1, 2026', 'Sept 21, 2026', '02-Jun-2026' or 'May 31/26'."""
    value = value.strip()
    if m := re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2}), (\d{4})", value): month, day, year = m[1], m[2], m[3]
    elif m := re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", value): day, month, year = m[1], m[2], m[3]
    elif m := re.fullmatch(r"([A-Za-z]+) (\d{1,2})/(\d{2})", value): month, day, year = m[1], m[2], "20" + m[3]
    else: raise ValueError(value)
    return date(int(year), MONTHS[month[:3].lower()], int(day))

def _find(pattern, text, what, provider, flags=0):
    if m := re.search(pattern, text, flags): return m
    raise BillImportError(f"Couldn't find the {what} on this {provider} bill.")

def _period(match):
    return (_date(match[1]), _date(match[2])) if match else (None, None)

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
    )

def parse_bill(text):
    """Turn the text of a supported bill into a ParsedBill, or raise BillImportError."""
    if "MUNICIPALITY OF NORTH GRENVILLE" in text.upper(): parser = _parse_north_grenville
    elif "Hydro One" in text and "Your account number is" in text: parser = _parse_hydro_one
    elif "enbridgegas.com" in text.lower(): parser = _parse_enbridge
    else: raise BillImportError("This isn't a bill the tracker can read yet. Supported: Hydro One, Enbridge Gas, North Grenville water.")
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

def _money_label(value): return f"${value:,.2f}"

def _date_label(value): return f"{value:%b} {value.day}, {value.year}"

def _provider_for(user, bill):
    return (Provider.objects.filter(owner__isnull=True, name=bill.provider).first()
            or Provider.objects.filter(owner=user, name__iexact=bill.provider).first()
            or Provider.objects.create(owner=user, name=bill.provider, category=bill.category, province_region="ON"))

def _account_for(user, provider, bill, property):
    for account in BillAccount.objects.filter(owner=user, provider=provider):
        if _digits(account.account_number) == _digits(bill.account_number): return account, False
    prop = match_property(user, bill.service_address) or property  # the chosen property is only a fallback
    if prop is None:
        raise BillImportError(f"{bill.provider} account {bill.account_number} isn't in the tracker yet, and its service address "
                              f"({bill.service_address}) doesn't match exactly one of your properties. Add the property, or choose it when you upload.")
    account = BillAccount(owner=user, provider=provider, property=prop, account_number=bill.account_number,
                          billing_frequency=bill.frequency, typical_amount=bill.amount_due, due_day=bill.due_date.day)
    try: account.full_clean()
    except ValidationError as exc: raise BillImportError(f"The new {bill.provider} account couldn't be saved: {exc.messages[0]}") from exc
    account.save()
    return account, True

def _settle_earlier(account, bill):
    """Record what this bill says about earlier ones: the payments it lists, and whether anything was carried forward."""
    current = (bill.statement_date.year, bill.statement_date.month)
    earlier = sorted((p for p in account.payments.all() if (p.billing_year, p.billing_month) < current),
                     key=lambda p: (p.billing_year, p.billing_month), reverse=True)
    settled = []
    if bill.payments and earlier and earlier[0].amount_paid < earlier[0].amount_due:
        latest = earlier[0]
        latest.amount_paid = min(latest.amount_due, max(latest.amount_paid, sum(amount for _, amount in bill.payments)))
        latest.payment_date = latest.payment_date or max(d for d, _ in bill.payments)
        latest.save(update_fields=["amount_paid", "payment_date", "updated_at"])
        settled.append(latest)
    if bill.previous_settled:
        for payment in earlier:
            if payment.amount_paid < payment.amount_due:
                payment.amount_paid = payment.amount_due
                payment.save(update_fields=["amount_paid", "updated_at"])
                if payment not in settled: settled.append(payment)
    return settled

def _import_one(user, bill, f, filename, property):
    provider = _provider_for(user, bill)
    account, new_account = _account_for(user, provider, bill, property)
    year, month = bill.statement_date.year, bill.statement_date.month
    if duplicate := account.payments.filter(statement_date=bill.statement_date).first():
        return ImportResult(filename, "duplicate", f"{bill.label}: already imported.", duplicate)
    payment = account.payments.filter(billing_year=year, billing_month=month).first()
    if payment and payment.statement_date:
        raise BillImportError(f"{bill.label}: a different bill dated {_date_label(payment.statement_date)} is already recorded "
                              f"for that month. Edit that record by hand.")
    outcome = "updated" if payment else "created"
    payment = payment or BillPayment(owner=user, bill_account=account, billing_year=year, billing_month=month)
    for name in ("statement_date", "due_date", "amount_due", "period_start", "period_end", "usage", "usage_unit"):
        setattr(payment, name, getattr(bill, name))
    payment.save()
    Attachment.objects.create(payment=payment, file=f, original_name=filename[:255])
    settled = _settle_earlier(account, bill)
    message = f"{bill.label}: {_money_label(bill.amount_due)} due {_date_label(bill.due_date)}"
    if new_account: message += f" (new account at {account.property})"
    if outcome == "updated": message += " (filled in the existing record)"
    if settled: message += "; marked " + ", ".join(p.period_label for p in settled) + " paid"
    return ImportResult(filename, outcome, message + ".", payment, settled)

def import_bills(user, files, property=None):
    """Import (file, filename) pairs for user. Unreadable files are reported and skipped; the rest go in oldest first."""
    results, parsed = [], []
    for f, filename in files:
        try: parsed.append((parse_bill(extract_text(f)), f, filename))
        except BillImportError as exc: results.append(ImportResult(filename, "error", f"{filename}: {exc}"))
    for bill, f, filename in sorted(parsed, key=lambda item: item[0].statement_date):
        try:
            with transaction.atomic(): results.append(_import_one(user, bill, f, filename, property))
        except BillImportError as exc: results.append(ImportResult(filename, "error", f"{filename}: {exc}"))
    return results
