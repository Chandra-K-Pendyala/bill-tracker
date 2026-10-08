import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from tracker.bill_import import BillImportError, _clean_name, _guess_category, detect_provider, import_bills, match_property, parse_bill, recognize_provider
from tracker.models import BillAccount, BillPayment, Property, Provider

FIXTURES = Path(__file__).parent / "fixtures"
MEDIA_ROOT = tempfile.mkdtemp()
def tearDownModule(): shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
def fixture(name): return (FIXTURES / name).read_text()
def pdf(name): return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")
def texts(mapping): return lambda f: mapping[f.name]
EXTRACT = "tracker.bill_import.extract_text"

class ReaderTests(SimpleTestCase):
    def test_toronto_hydro_autopay(self):
        bill = parse_bill(fixture("toronto_hydro_autopay.txt"))
        self.assertEqual(bill.provider, "Toronto Hydro")
        self.assertEqual(bill.category, "electricity")
        self.assertEqual(bill.account_number, "5500000001")
        self.assertEqual(bill.service_address, "12 MAPLE ST (E) SUITE 3, TORONTO")
        self.assertEqual(bill.statement_date, date(2026, 7, 10))
        self.assertEqual(bill.due_date, date(2026, 8, 3))
        self.assertEqual(bill.amount_due, Decimal("164.98"))
        self.assertEqual(bill.amount_to_pay, Decimal("210.47"))
        self.assertEqual(bill.carried, Decimal("45.49"))
        self.assertEqual(bill.period_start, date(2026, 6, 1))
        self.assertEqual(bill.period_end, date(2026, 7, 3))
        self.assertEqual(bill.usage, Decimal("904.961"))
        self.assertEqual(bill.usage_unit, "kWh")
        self.assertEqual(bill.payments, [])
        self.assertFalse(bill.previous_settled)
        self.assertEqual(bill.autopay_date, date(2026, 8, 3))
        self.assertFalse(bill.needs_review)

    def test_toronto_hydro_payments(self):
        bill = parse_bill(fixture("toronto_hydro_payments.txt"))
        self.assertEqual(bill.statement_date, date(2026, 8, 10))
        self.assertEqual(bill.due_date, date(2026, 9, 3))
        self.assertEqual(bill.amount_due, Decimal("164.92"))
        self.assertEqual(bill.carried, Decimal("0.00"))
        self.assertTrue(bill.previous_settled)
        self.assertEqual(bill.autopay_date, date(2026, 9, 3))
        self.assertEqual(bill.payments, [(date(2026, 7, 15), Decimal("45.49")), (date(2026, 7, 31), Decimal("164.98"))])

    def test_wyse_credit(self):
        bill = parse_bill(fixture("wyse_credit.txt"))
        self.assertEqual(bill.provider, "Wyse Meter Solutions")
        self.assertEqual(bill.category, "water")
        self.assertEqual(bill.account_number, "36000000-01")
        self.assertEqual(bill.service_address, "3-12 Maple St")
        self.assertEqual(bill.statement_date, date(2026, 6, 18))
        self.assertEqual(bill.due_date, date(2026, 7, 13))
        self.assertEqual(bill.amount_due, Decimal("114.01"))
        self.assertEqual(bill.amount_to_pay, Decimal("0.00"))
        self.assertEqual(bill.carried, Decimal("-142.27"))
        self.assertEqual(bill.usage, Decimal("2611.00"))
        self.assertEqual(bill.usage_unit, "gal")
        self.assertEqual(bill.period_start, date(2026, 5, 1))
        self.assertEqual(bill.period_end, date(2026, 6, 1))
        self.assertTrue(bill.previous_settled)

    def test_wyse_arrears(self):
        bill = parse_bill(fixture("wyse_arrears.txt"))
        self.assertEqual(bill.statement_date, date(2026, 8, 19))
        self.assertEqual(bill.due_date, date(2026, 9, 14))
        self.assertEqual(bill.amount_due, Decimal("114.55"))
        self.assertEqual(bill.amount_to_pay, Decimal("150.04"))
        self.assertEqual(bill.carried, Decimal("38.66"))
        self.assertFalse(bill.previous_settled)
        self.assertIn("$38.66 carried", bill.note)

    def test_general_reader_single_column(self):
        bill = parse_bill(fixture("general_single_column.txt"))
        self.assertEqual(bill.provider, "Alectra Utilities")
        self.assertEqual(bill.category, "electricity")
        self.assertEqual(bill.account_number, "7712 3456 01")
        self.assertEqual(bill.service_address, "12 MAPLE ST, HAMILTON")
        self.assertEqual(bill.statement_date, date(2026, 8, 15))
        self.assertEqual(bill.due_date, date(2026, 9, 5))
        self.assertEqual(bill.amount_due, Decimal("104.76"))
        self.assertEqual(bill.period_start, date(2026, 7, 10))
        self.assertEqual(bill.period_end, date(2026, 8, 9))
        self.assertTrue(bill.needs_review)

    def test_general_reader_reads_columns(self):
        bill = parse_bill(fixture("general_columns.txt"))
        self.assertEqual(bill.provider, "Hydro Ottawa")
        self.assertEqual(bill.account_number, "3311 2244 55")
        self.assertEqual(bill.statement_date, date(2026, 8, 20))
        self.assertEqual(bill.due_date, date(2026, 9, 10))
        self.assertEqual(bill.amount_due, Decimal("87.65"))
        self.assertTrue(bill.needs_review)

    def test_company_name_is_read_from_the_bill(self):
        bill = parse_bill(fixture("general_unknown_provider.txt"))
        self.assertEqual((bill.provider, bill.category), ("Lakeview Propane Co-op", "other"))
        self.assertTrue(bill.provider_detected)
        self.assertEqual(bill.account_number, "LP-204871")
        self.assertEqual(bill.statement_date, date(2026, 9, 1))
        self.assertEqual(bill.due_date, date(2026, 9, 21))
        self.assertEqual(bill.amount_due, Decimal("240.01"))
        self.assertTrue(bill.needs_review)
        self.assertIn("read from the bill", bill.note)

    def test_chosen_provider_beats_the_name_on_the_bill(self):
        bill = parse_bill(fixture("general_unknown_provider.txt"), ("Lakeview Gas", "natural_gas"))
        self.assertEqual((bill.provider, bill.category, bill.provider_detected), ("Lakeview Gas", "natural_gas", False))

    def test_bill_without_a_company_name_needs_a_choice(self):
        text = "Statement\nAccount Number: 4455667\nBill Date: Sep 1, 2026\nTotal Amount Due   $12.50\nDue Date: Sep 20, 2026\n"
        with self.assertRaisesMessage(BillImportError, "company's name couldn't be found"):
            parse_bill(text)
        self.assertEqual(parse_bill(text, ("Corner Store", "other")).amount_due, Decimal("12.50"))

    def test_recognize_provider(self):
        self.assertEqual(recognize_provider("Your bill from Elexicon Energy Inc."), ("Elexicon Energy", "electricity"))
        self.assertEqual(recognize_provider("Pay at rogers.com/billing"), ("Rogers", "internet"))
        self.assertEqual(recognize_provider("CITY OF TORONTO Utility Bill"), ("City of Toronto", "water"))
        self.assertIsNone(recognize_provider("Nothing to see here"))

@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="x-safe-pass-123")
        cls.home = Property.objects.create(owner=cls.user, name="Maple", ownership="rented", street_address="12 Maple St, Suite 3", city="Toronto", province="ON", postal_code="M4M 1A1")
        cls.th = Provider.objects.create(name="Toronto Hydro", category="electricity", is_default=True, is_custom=False)
        cls.wyse = Provider.objects.create(name="Wyse Meter Solutions", category="water", is_default=True, is_custom=False)

    def account(self, provider, number):
        return BillAccount.objects.create(owner=self.user, provider=provider, property=self.home, account_number=number, service_address="12 Maple St", city="Toronto", province="ON", postal_code="M4M 1A1", due_day=3)

    def bill(self, account, month, amount):
        return BillPayment.objects.create(owner=self.user, bill_account=account, billing_month=month, billing_year=2026, amount_due=Decimal(amount), due_date=date(2026, month, 28))

    def run_import(self, *names, provider=None):
        with mock.patch(EXTRACT, side_effect=texts({n: fixture(n) for n in names})):
            return import_bills(self.user, [(pdf(n), n) for n in names], provider=provider)

    def test_autopay_schedules_the_bill_and_the_carried_balance(self):
        account = self.account(self.th, "5500000001")
        june = self.bill(account, 6, "45.49")
        results = self.run_import("toronto_hydro_autopay.txt")
        self.assertEqual(results[0].outcome, "created")
        july = results[0].payment
        july.refresh_from_db()
        self.assertEqual(july.amount_paid, Decimal("164.98"))
        self.assertEqual(july.payment_date, date(2026, 8, 3))
        self.assertEqual(july.payment_method, "auto_pay")
        self.assertEqual(july.status, "paid")
        june.refresh_from_db()
        self.assertEqual(june.amount_paid, Decimal("45.49"))
        self.assertEqual(june.payment_date, date(2026, 8, 3))
        self.assertEqual(june.payment_method, "auto_pay")
        account.refresh_from_db()
        self.assertTrue(account.auto_pay)

    def test_payments_on_the_next_bill_correct_the_dates(self):
        account = self.account(self.th, "5500000001")
        june = self.bill(account, 6, "45.49")
        self.run_import("toronto_hydro_autopay.txt")
        self.run_import("toronto_hydro_payments.txt")
        june.refresh_from_db()
        self.assertEqual(june.payment_date, date(2026, 7, 15))
        self.assertEqual(june.payment_method, "")
        july = BillPayment.objects.get(bill_account=account, billing_month=7)
        self.assertEqual(july.payment_date, date(2026, 7, 31))
        self.assertEqual(july.payment_method, "")
        august = BillPayment.objects.get(bill_account=account, billing_month=8)
        self.assertEqual(august.payment_date, date(2026, 9, 3))
        self.assertEqual(august.payment_method, "auto_pay")
        self.assertEqual(august.amount_paid, Decimal("164.92"))

    def test_credit_covers_the_bill_and_settles_earlier_ones(self):
        account = self.account(self.wyse, "36000000-01")
        may = self.bill(account, 5, "100.00")
        self.run_import("wyse_credit.txt")
        june = BillPayment.objects.get(bill_account=account, billing_month=6)
        june.refresh_from_db()
        self.assertEqual(june.amount_paid, Decimal("114.01"))
        self.assertEqual(june.status, "paid")
        self.assertIsNone(june.payment_date)
        may.refresh_from_db()
        self.assertEqual(may.status, "paid")
        self.assertIn("credit of $142.27", june.notes)

    def test_arrears_leave_the_earlier_bill_unpaid(self):
        account = self.account(self.wyse, "36000000-01")
        july = self.bill(account, 7, "98.40")
        self.run_import("wyse_arrears.txt")
        august = BillPayment.objects.get(bill_account=account, billing_month=8)
        august.refresh_from_db()
        self.assertEqual(august.amount_due, Decimal("114.55"))
        self.assertEqual(august.amount_paid, Decimal("0"))
        self.assertEqual(august.status, "unpaid")
        july.refresh_from_db()
        self.assertEqual(july.status, "unpaid")

    def test_general_reader_flags_the_bill(self):
        results = self.run_import("general_single_column.txt")
        self.assertEqual(results[0].outcome, "created")
        payment = results[0].payment
        self.assertTrue(payment.needs_review)
        self.assertEqual(payment.amount_due, Decimal("104.76"))
        self.assertEqual(payment.bill_account.provider.name, "Alectra Utilities")
        self.assertIn("check it", results[0].message)

    def test_new_company_is_added_from_the_bill(self):
        results = self.run_import("general_unknown_provider.txt")
        self.assertEqual(results[0].outcome, "created")
        self.assertIn("new provider", results[0].message)
        provider = results[0].payment.bill_account.provider
        self.assertEqual((provider.name, provider.category, provider.owner, provider.is_custom), ("Lakeview Propane Co-op", "other", self.user, True))
        self.assertTrue(results[0].payment.needs_review)

    def test_later_bills_find_the_account_after_the_provider_is_renamed(self):
        first = self.run_import("general_unknown_provider.txt")[0].payment
        provider = first.bill_account.provider
        provider.name = "Lakeview Co-op"
        provider.save()
        october = fixture("general_unknown_provider.txt").replace("2026-09-01", "2026-10-01").replace("2026-09-21", "2026-10-21")
        with mock.patch(EXTRACT, return_value=october):
            result = import_bills(self.user, [(pdf("oct.pdf"), "oct.pdf")])[0]
        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.payment.bill_account, first.bill_account)
        self.assertEqual(Provider.objects.filter(owner=self.user).count(), 1)

    def test_chosen_provider_is_used_on_import(self):
        other = Provider.objects.create(owner=self.user, name="Lakeview Gas", category="natural_gas")
        results = self.run_import("general_unknown_provider.txt", provider=other)
        self.assertEqual(results[0].outcome, "created")
        self.assertEqual(results[0].payment.bill_account.provider, other)
        self.assertTrue(results[0].payment.needs_review)

    def test_same_bill_twice_is_skipped_even_for_a_new_company(self):
        self.run_import("general_unknown_provider.txt")
        results = self.run_import("general_unknown_provider.txt")
        self.assertEqual(results[0].outcome, "duplicate")
        self.assertEqual(BillPayment.objects.count(), 1)

@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="x-safe-pass-123")
        cls.home = Property.objects.create(owner=cls.user, name="Maple", ownership="rented", street_address="12 Maple St", city="Toronto", province="ON", postal_code="M4M 1A1")
        cls.provider = Provider.objects.create(name="Wyse Meter Solutions", category="water", is_default=True, is_custom=False)
        cls.account = BillAccount.objects.create(owner=cls.user, provider=cls.provider, property=cls.home, account_number="36000000-01", service_address="12 Maple St", city="Toronto", province="ON", postal_code="M4M 1A1")
        cls.other_account = BillAccount.objects.create(owner=cls.user, provider=cls.provider, property=cls.home, account_number="99999999-01", service_address="12 Maple St", city="Toronto", province="ON", postal_code="M4M 1A1")

    def bill(self, account, month, amount, **extra):
        return BillPayment.objects.create(owner=self.user, bill_account=account, billing_month=month, billing_year=2026, amount_due=Decimal(amount), due_date=date(2026, month, 14), **extra)

    def setUp(self):
        self.client.force_login(self.user)

    def test_mark_paid_settles_earlier_bills_of_the_same_account(self):
        july = self.bill(self.account, 7, "98.40")
        august = self.bill(self.account, 8, "99.60")
        elsewhere = self.bill(self.other_account, 7, "50.00")
        self.client.post(reverse("payment_mark_paid", args=[august.pk]))
        july.refresh_from_db()
        august.refresh_from_db()
        elsewhere.refresh_from_db()
        self.assertEqual(august.status, "paid")
        self.assertEqual(july.status, "paid")
        self.assertEqual(elsewhere.status, "unpaid")

    def test_mark_paid_leaves_later_bills_alone(self):
        july = self.bill(self.account, 7, "98.40")
        august = self.bill(self.account, 8, "99.60")
        self.client.post(reverse("payment_mark_paid", args=[july.pk]))
        july.refresh_from_db()
        august.refresh_from_db()
        self.assertEqual(july.status, "paid")
        self.assertEqual(august.status, "unpaid")

    def test_saving_a_flagged_bill_clears_the_flag(self):
        payment = self.bill(self.account, 9, "80.00", needs_review=True)
        self.client.post(reverse("payment_edit", args=[payment.pk]), data={
            "bill_account": self.account.pk,
            "billing_month": "9",
            "billing_year": "2026",
            "statement_date": "",
            "amount_due": "80.00",
            "due_date": "2026-09-14",
            "amount_paid": "0",
            "payment_date": "",
            "payment_method": "",
            "reference_number": "",
            "period_start": "",
            "period_end": "",
            "usage": "",
            "usage_unit": "",
            "notes": ""
        })
        payment.refresh_from_db()
        self.assertFalse(payment.needs_review)

    def test_review_filter_and_dashboard_notice(self):
        flagged = self.bill(self.account, 9, "80.00", needs_review=True)
        self.bill(self.account, 8, "70.00")
        response = self.client.get(reverse("payment_list"), {"review": "1"})
        self.assertEqual(list(response.context["payments"]), [flagged])
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["review_count"], 1)
        self.assertContains(response, "needs a check")

    def test_scheduled_withdrawal_status(self):
        later = timezone.localdate() + timedelta(days=5)
        payment = self.bill(self.account, 10, "60.00", amount_paid=Decimal("60.00"), payment_date=later, payment_method="auto_pay")
        self.assertEqual(payment.effective_status, "scheduled")
        self.assertEqual(payment.effective_status_label, "Auto-pay scheduled")
        payment.payment_date = timezone.localdate() - timedelta(days=1)
        payment.save()
        self.assertEqual(payment.effective_status, "paid")


class DetectProviderTests(SimpleTestCase):
    def test_payee_line(self):
        text = "Statement\nPlease make cheques payable to: Northern Lights Water Co.\nAccount Number: 99887766\n"
        self.assertEqual(detect_provider(text), ("Northern Lights Water", "water"))

    def test_payee_printed_under_the_label_next_to_the_customer(self):
        text = ("                                                Make Cheque Payable to\n"
                "    SAMPLE, JANE                                TOWNSHIP OF EXAMPLE VALLEY\n"
                "    12 MAPLE ST                                 PO BOX 1\n")
        self.assertEqual(detect_provider(text)[0], "Township of Example Valley")

    def test_bodies_that_are_not_the_biller_are_skipped(self):
        text = ("Ontario Energy Board rules apply to this bill.\nBluebird Power Inc.\n"
                "Pay at bluebirdpower.ca\nUsage this month: 512 kWh\n")
        self.assertEqual(detect_provider(text), ("Bluebird Power", "electricity"))

    def test_generic_phrases_are_not_companies(self):
        self.assertIsNone(detect_provider("Your Water Charges\nNatural Gas\nTotal Amount Due $10.00\n"))

    def test_clean_name(self):
        self.assertEqual(_clean_name("LAKEVIEW PROPANE CO-OP"), "Lakeview Propane Co-op")
        self.assertEqual(_clean_name("Hydro Example Networks Inc."), "Hydro Example Networks")
        self.assertEqual(_clean_name("MUNICIPALITY OF NORTH EXAMPLE"), "Municipality of North Example")

    def test_guess_category(self):
        self.assertEqual(_guess_category("Usage: 640 kWh", "Acme Utilities"), "electricity")
        self.assertEqual(_guess_category("Tier 1 - 1710 GAL", "Acme Meter Solutions"), "water")
        self.assertEqual(_guess_category("Monthly plan", "Acme Wireless"), "mobile")
        self.assertEqual(_guess_category("Delivered 300 L", "Acme Propane"), "other")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class PropertyMatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="x-safe-pass-123")
        Provider.objects.create(name="Toronto Hydro", category="electricity", is_default=True, is_custom=False)

    def home(self, name, street, postal="K1A 0A1"):
        return Property.objects.create(owner=self.user, name=name, ownership="owned", street_address=street, city="Toronto", province="ON", postal_code=postal)

    def test_street_number_and_name(self):
        home = self.home("De Grassi", "52 De Grassi St, Unit 1")
        self.assertEqual(match_property(self.user, "UNIT 1-52 DE GRASSI ST"), (home, "street"))

    def test_unit_decides_between_homes_on_one_street(self):
        four, nine = self.home("Four", "7 Elm Way, Unit 4"), self.home("Nine", "7 Elm Way, Unit 9")
        self.assertEqual(match_property(self.user, "9-7 Elm Way"), (nine, "unit"))
        self.assertEqual(match_property(self.user, "7 ELM WAY (E) SUITE 4, TORONTO"), (four, "unit"))

    def test_street_name_typed_a_little_differently(self):
        home = self.home("Lake", "7 Lakshore Way")
        self.assertEqual(match_property(self.user, "7 LAKESHORE WAY"), (home, "similar street name"))
        home.street_address = "7 LakeshoreWay"
        home.save()
        self.assertEqual(match_property(self.user, "7 LAKESHORE WAY"), (home, "similar street name"))

    def test_postal_code_when_the_street_does_not_match(self):
        home = self.home("Maple", "Unit 3", postal="M4M 1A1")
        self.assertEqual(match_property(self.user, "3-12 MAPLE ST", ("M4M1A1",)), (home, "postal code"))

    def test_street_wins_over_a_mailing_postal_code(self):
        grassi = self.home("De Grassi", "52 De Grassi St", postal="K1A 0A1")
        self.home("Maple", "12 Maple St", postal="M4M 1A1")
        self.assertEqual(match_property(self.user, "52 DE GRASSI ST", ("M4M1A1",)), (grassi, "street"))

    def test_nothing_clear(self):
        self.home("A", "7 Elm Way", postal="M4M 1A1")
        self.home("B", "7 Elm Way", postal="M4M 1A1")
        self.assertEqual(match_property(self.user, "7 ELM WAY", ("M4M1A1",)), (None, None))

    def test_import_says_how_the_property_was_found(self):
        self.home("Maple", "Unit 3", postal="M4M 1A1")
        with mock.patch(EXTRACT, return_value=fixture("toronto_hydro_autopay.txt")):
            result = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])[0]
        self.assertEqual(result.outcome, "created")
        self.assertIn("matched by postal code", result.message)

    def test_error_lists_your_properties(self):
        self.home("Cottage", "1 Lake Rd", postal="K7L 1A1")
        with mock.patch(EXTRACT, return_value=fixture("toronto_hydro_autopay.txt")):
            result = import_bills(self.user, [(pdf("jul.pdf"), "jul.pdf")])[0]
        self.assertEqual(result.outcome, "error")
        self.assertIn("Cottage (1 Lake Rd, K7L 1A1)", result.message)
