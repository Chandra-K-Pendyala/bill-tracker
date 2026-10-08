import re
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest import skipUnless
from django.test import SimpleTestCase, TestCase, override_settings
from tracker.bill_import import BillImportError, extract_text, import_bills, parse_bill, street_key
from tracker.models import Attachment, BillAccount, BillPayment, Property, Provider

FIXTURES = Path(__file__).parent / "fixtures"
MEDIA_ROOT = tempfile.mkdtemp()
def tearDownModule(): shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
def fixture(name): return (FIXTURES / name).read_text()
def pdf(name): return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")
def texts(mapping): return lambda f: mapping[f.name]
EXTRACT = "tracker.bill_import.extract_text"

def minimal_pdf(text):
    """A one-page PDF whose text layer is `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)

class ExtractTextTests(SimpleTestCase):
    def test_rejects_files_that_are_not_pdfs(self):
        with self.assertRaisesMessage(BillImportError, "isn't a PDF"):
            extract_text(SimpleUploadedFile("bill.pdf", b"<html>not a pdf</html>"))

    @skipUnless(shutil.which("pdftotext"), "needs poppler-utils")
    def test_reads_the_text_layer(self):
        upload = SimpleUploadedFile("bill.pdf", minimal_pdf("Total Amount Due 12.34"))
        self.assertIn("Total Amount Due 12.34", extract_text(upload))
        self.assertEqual(upload.read(5), b"%PDF-")  # rewound so the file can still be saved

class ParseBillTests(SimpleTestCase):
    def test_hydro_one(self):
        bill = parse_bill(fixture("hydro_one.txt"))
        self.assertEqual(bill.provider, "Hydro One")
        self.assertEqual(bill.category, "electricity")
        self.assertEqual(bill.frequency, "monthly")
        self.assertEqual(bill.account_number, "1234 5678 9012")
        self.assertEqual(bill.service_address, "12 MAPLE ST")
        self.assertEqual(bill.statement_date, date(2026, 7, 2))
        self.assertEqual(bill.due_date, date(2026, 7, 22))
        self.assertEqual(bill.amount_due, Decimal("99.40"))
        self.assertEqual(bill.period_start, date(2026, 5, 25))
        self.assertEqual(bill.period_end, date(2026, 6, 24))
        self.assertEqual(bill.usage, Decimal("433.0"))
        self.assertEqual(bill.usage_unit, "kWh")
        self.assertTrue(bill.previous_settled)
        self.assertEqual(bill.payments, [(date(2026, 5, 31), Decimal("75.47"))])

    def test_enbridge(self):
        bill = parse_bill(fixture("enbridge.txt"))
        self.assertEqual(bill.provider, "Enbridge Gas")
        self.assertEqual(bill.category, "natural_gas")
        self.assertEqual(bill.account_number, "91 00 00 12345 6")
        self.assertEqual(bill.service_address, "12 MAPLE ST UNIT 3 TORONTO ON M4M 1A1")
        self.assertEqual(bill.statement_date, date(2026, 9, 1))
        self.assertEqual(bill.due_date, date(2026, 9, 21))
        self.assertEqual(bill.amount_due, Decimal("47.84"))
        self.assertEqual(bill.period_start, date(2026, 7, 31))
        self.assertEqual(bill.period_end, date(2026, 8, 29))
        self.assertEqual(bill.usage, Decimal("36"))
        self.assertEqual(bill.usage_unit, "m³")
        self.assertTrue(bill.previous_settled)
        self.assertEqual(bill.payments, [(date(2026, 8, 11), Decimal("40.63"))])

    def test_enbridge_first_bill_includes_new_account_charge(self):
        bill = parse_bill(fixture("enbridge_first_bill.txt"))
        self.assertEqual(bill.statement_date, date(2026, 7, 2))
        self.assertEqual(bill.due_date, date(2026, 7, 22))
        self.assertEqual(bill.amount_due, Decimal("79.69"))
        self.assertEqual(bill.payments, [])

    def test_north_grenville_water(self):
        bill = parse_bill(fixture("north_grenville.txt"))
        self.assertEqual(bill.provider, "Municipality of North Grenville")
        self.assertEqual(bill.category, "water")
        self.assertEqual(bill.frequency, "bi_monthly")
        self.assertEqual(bill.account_number, "031 999999 001")
        self.assertEqual(bill.service_address, "12 MAPLE ST")
        self.assertEqual(bill.statement_date, date(2026, 10, 6))
        self.assertEqual(bill.due_date, date(2026, 10, 30))
        self.assertEqual(bill.amount_due, Decimal("279.10"))
        self.assertEqual(bill.period_start, date(2026, 8, 1))
        self.assertEqual(bill.period_end, date(2026, 9, 30))
        self.assertEqual(bill.usage, Decimal("43.30"))
        self.assertEqual(bill.usage_unit, "m³")
        self.assertTrue(bill.previous_settled)
        self.assertEqual(bill.payments, [])

    def test_water_bill_with_arrears(self):
        text = re.sub(r"(Account Balance Due By [^$\n]*\$\s*)279\.10", r"\g<1>316.53", fixture("north_grenville.txt"))
        bill = parse_bill(text)
        self.assertEqual(bill.amount_due, Decimal("279.10"))
        self.assertFalse(bill.previous_settled)

    def test_unknown_text_raises(self):
        with self.assertRaises(BillImportError):
            parse_bill("Invoice from another company")

    def test_street_key(self):
        self.assertEqual(street_key("52 De Grassi St, Unit 1"), ("52", "DEGRASSI"))
        self.assertEqual(street_key("UNIT 1-52 DE GRASSI ST"), ("52", "DEGRASSI"))
        self.assertEqual(street_key("7 ELM WAY UNIT 4 TORONTO ON M4M 1A1"), ("7", "ELM"))
        self.assertEqual(street_key("123 1st Ave"), ("123", "1ST"))
        self.assertIsNone(street_key("no number here"))

@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ImportBillsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="x-safe-pass-123")
        cls.other = User.objects.create_user("bob", password="x-safe-pass-456")
        cls.hydro = Provider.objects.create(name="Hydro One", category="electricity", is_default=True, is_custom=False)
        Provider.objects.create(name="Enbridge Gas", category="natural_gas", is_default=True, is_custom=False)
        cls.home = Property.objects.create(owner=cls.user, name="Maple", ownership="owned", street_address="12 Maple St, Unit 1", city="Ottawa", province="ON", postal_code="K1A 0A1")

    def make_account(self):
        return BillAccount.objects.create(owner=self.user, provider=self.hydro, property=self.home, account_number="123456789012", service_address="12 Maple St", city="Ottawa", province="ON", postal_code="K1A 0A1", due_day=21)

    def make_payment(self, account, month, amount, statement=None):
        return BillPayment.objects.create(owner=self.user, bill_account=account, billing_month=month, billing_year=2026, amount_due=Decimal(amount), due_date=date(2026, month, 21), statement_date=statement)

    def test_import_creates_account_payment_and_attachment(self):
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "created")
        account = BillAccount.objects.get(owner=self.user)
        self.assertEqual(account.property, self.home)
        self.assertEqual(account.provider, self.hydro)
        self.assertEqual(account.account_number, "1234 5678 9012")
        self.assertEqual(account.city, "Ottawa")
        payment = results[0].payment
        self.assertEqual(payment.billing_month, 7)
        self.assertEqual(payment.billing_year, 2026)
        self.assertEqual(payment.amount_due, Decimal("99.40"))
        self.assertEqual(payment.amount_paid, Decimal("0"))
        self.assertEqual(payment.status, "unpaid")
        self.assertEqual(payment.due_date, date(2026, 7, 22))
        self.assertEqual(payment.usage, Decimal("433.0"))
        self.assertEqual(payment.usage_unit, "kWh")
        self.assertEqual(payment.attachments.get().original_name, "jul.pdf")

    def test_same_bill_twice_is_a_duplicate(self):
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt"), "copy.pdf": fixture("hydro_one.txt")})):
            import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
            results = import_bills(self.user, [(pdf("copy.pdf"), "copy.pdf")])
        self.assertEqual(results[0].outcome, "duplicate")
        self.assertEqual(BillPayment.objects.count(), 1)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_duplicate_within_one_upload(self):
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt"), "copy.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf"), (pdf("copy.pdf"), "copy.pdf")])
        self.assertEqual([r.outcome for r in results], ["created", "duplicate"])
        self.assertEqual(Attachment.objects.count(), 1)

    def test_ambiguous_address_needs_a_property(self):
        Property.objects.create(owner=self.user, name="Maple 2", ownership="rented", street_address="12 Maple St, Unit 2", city="Ottawa", province="ON", postal_code="K1A 0A1")
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(results[0].outcome, "error")
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")], property=self.home)
        self.assertEqual(results[0].outcome, "created")

    def test_later_bill_marks_earlier_bill_paid(self):
        account = self.make_account()
        june = self.make_payment(account, 6, "75.47", date(2026, 6, 1))
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(results[0].outcome, "created")
        self.assertEqual(results[0].settled, [june])
        self.assertEqual(BillAccount.objects.count(), 1)
        june.refresh_from_db()
        self.assertEqual(june.amount_paid, Decimal("75.47"))
        self.assertEqual(june.payment_date, date(2026, 5, 31))
        self.assertEqual(june.status, "paid")

    def test_settled_balance_marks_all_earlier_bills_paid(self):
        account = self.make_account()
        may = self.make_payment(account, 5, "50.00")
        june = self.make_payment(account, 6, "75.47")
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(results[0].outcome, "created")
        self.assertEqual(results[0].settled, [june, may])
        self.assertEqual(BillAccount.objects.count(), 1)
        june.refresh_from_db()
        self.assertEqual(june.amount_paid, Decimal("75.47"))
        self.assertEqual(june.payment_date, date(2026, 5, 31))
        self.assertEqual(june.status, "paid")
        may.refresh_from_db()
        self.assertEqual(may.amount_paid, Decimal("50.00"))
        self.assertIsNone(may.payment_date)
        self.assertEqual(may.status, "paid")

    def test_batch_is_imported_oldest_first(self):
        with mock.patch(EXTRACT, side_effect=texts({"sep.pdf": fixture("enbridge.txt"), "jul.pdf": fixture("enbridge_first_bill.txt")})):
            results = import_bills(self.user, [(pdf("sep.pdf"), "sep.pdf"), (pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual([r.filename for r in results], ["jul.pdf", "sep.pdf"])
        self.assertEqual(results[0].outcome, "created")
        self.assertEqual(results[1].outcome, "created")
        july = BillPayment.objects.get(billing_month=7)
        self.assertEqual(july.amount_due, Decimal("79.69"))
        self.assertEqual(july.amount_paid, Decimal("79.69"))
        self.assertEqual(july.status, "paid")

    def test_unmatched_address_without_property_is_an_error(self):
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.other, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(results[0].outcome, "error")
        self.assertIn("doesn't match one of your properties", results[0].message)
        self.assertFalse(BillAccount.objects.filter(owner=self.other).exists())

    def test_explicit_property_is_used_for_new_accounts(self):
        cabin = Property.objects.create(owner=self.other, name="Cabin", ownership="rented", street_address="1 Lake Rd", city="Kingston", province="ON", postal_code="K7L 1A1")
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.other, [(pdf("jul.pdf"), "jul.pdf")], property=cabin)
        self.assertEqual(results[0].outcome, "created")
        account = BillAccount.objects.get(owner=self.other)
        self.assertEqual(account.property, cabin)

    def test_unreadable_file_is_reported(self):
        with mock.patch(EXTRACT, side_effect=BillImportError("This file isn't a PDF.")):
            results = import_bills(self.user, [(pdf("x.pdf"), "x.pdf")])
        self.assertEqual(results[0].outcome, "error")
        self.assertIn("isn't a PDF", results[0].message)
        self.assertEqual(BillPayment.objects.count(), 0)

    def test_different_bill_for_same_month_is_refused(self):
        account = self.make_account()
        other_bill = self.make_payment(account, 7, "10.00", date(2026, 7, 15))
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(results[0].outcome, "error")
        self.assertIn("already recorded", results[0].message)
        other_bill.refresh_from_db()
        self.assertEqual(other_bill.amount_due, Decimal("10.00"))

    def test_manual_record_for_same_month_is_filled_in(self):
        account = self.make_account()
        manual = self.make_payment(account, 7, "80.00")
        with mock.patch(EXTRACT, side_effect=texts({"jul.pdf": fixture("hydro_one.txt")})):
            results = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])
        self.assertEqual(results[0].outcome, "updated")
        manual.refresh_from_db()
        self.assertEqual(manual.amount_due, Decimal("99.40"))
        self.assertEqual(manual.statement_date, date(2026, 7, 2))
        self.assertEqual(manual.due_date, date(2026, 7, 22))
        self.assertEqual(account.payments.count(), 1)
