import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from tracker.models import Attachment, BillAccount, BillPayment, Property, Provider

MEDIA_ROOT = tempfile.mkdtemp()
def tearDownModule(): shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
HYDRO_TEXT = (Path(__file__).parent / "fixtures" / "hydro_one.txt").read_text()
PROPERTY = {"name": "Cabin", "ownership": "rented", "street_address": "1 Lake Rd", "city": "Kingston", "province": "ON",
            "postal_code": "k7l 1a1", "active": "on", "notes": ""}
ACCOUNT = {"email": "", "service_address": "", "city": "", "province": "", "postal_code": "", "country": "Canada",
           "billing_frequency": "monthly", "typical_amount": "10", "due_day": "5", "active": "on", "notes": ""}
PAYMENT = {"billing_month": "1", "billing_year": "2021", "statement_date": "", "amount_due": "50.00", "due_date": "2021-01-30",
           "amount_paid": "20.00", "payment_date": "2021-01-10", "payment_method": "", "reference_number": "", "period_start": "",
           "period_end": "", "usage": "", "usage_unit": "", "notes": ""}

@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.alice = User.objects.create_user("alice", password="x-safe-pass-123")
        cls.bob = User.objects.create_user("bob", password="x-safe-pass-456")
        cls.hydro = Provider.objects.create(name="Hydro One", category="electricity", is_default=True, is_custom=False)
        cls.home = Property.objects.create(owner=cls.alice, name="Maple", ownership="owned", street_address="12 Maple St", city="Ottawa", province="ON", postal_code="K1A 0A1")
        cls.account = BillAccount.objects.create(owner=cls.alice, provider=cls.hydro, property=cls.home, account_number="ACC-1", service_address="12 Maple St", city="Ottawa", province="ON", postal_code="K1A 0A1", typical_amount=Decimal("100"), due_day=20)
        cls.payment = BillPayment.objects.create(owner=cls.alice, bill_account=cls.account, billing_month=cls.today.month, billing_year=cls.today.year, amount_due=Decimal("100.00"), due_date=cls.today + timedelta(days=10))

    def bill(self, month, due_offset, paid="0"):
        return BillPayment.objects.create(owner=self.alice, bill_account=self.account, billing_month=month, billing_year=2020, amount_due=Decimal("40.00"), amount_paid=Decimal(paid), due_date=self.today + timedelta(days=due_offset))

    def test_new_pages_require_login(self):
        for url in [reverse("property_list"), reverse("property_add"), reverse("bill_import"), reverse("account_detail", args=[self.account.pk])]:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_pages_render_for_owner(self):
        self.client.force_login(self.alice)
        urls = [reverse(name) for name in ["dashboard", "property_list", "property_add", "bill_import", "reports", "reminders", "payment_list"]]
        urls += [reverse("account_detail", args=[self.account.pk]), reverse("payment_add") + f"?account={self.account.pk}",
                 reverse("payment_edit", args=[self.payment.pk]), reverse("property_edit", args=[self.home.pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_other_users_objects_are_hidden(self):
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(reverse("property_edit", args=[self.home.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("property_delete", args=[self.home.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("account_detail", args=[self.account.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("payment_mark_paid", args=[self.payment.pk])).status_code, 404)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "unpaid")

    def test_create_property(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("property_add"), PROPERTY)
        self.assertRedirects(response, reverse("property_list"))
        cabin = Property.objects.get(name="Cabin")
        self.assertEqual((cabin.owner, cabin.ownership, cabin.postal_code), (self.alice, "rented", "K7L 1A1"))

    def test_duplicate_property_name_is_rejected(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("property_add"), {**PROPERTY, "name": "maple"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Property.objects.filter(owner=self.alice).count(), 1)

    def test_property_with_accounts_is_not_deleted(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("property_delete", args=[self.home.pk]))
        self.assertRedirects(response, reverse("property_list"))
        self.assertTrue(Property.objects.filter(pk=self.home.pk).exists())

    def test_account_address_comes_from_property(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("account_add"), {**ACCOUNT, "property": self.home.pk, "provider": self.hydro.pk, "account_number": "NEW-1"})
        account = BillAccount.objects.get(account_number="NEW-1")
        self.assertRedirects(response, reverse("account_detail", args=[account.pk]))
        self.assertEqual((account.service_address, account.city, account.province, account.postal_code), ("12 Maple St", "Ottawa", "ON", "K1A 0A1"))

    def test_account_needs_property_or_address(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("account_add"), {**ACCOUNT, "property": "", "provider": self.hydro.pk, "account_number": "NEW-2"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillAccount.objects.filter(account_number="NEW-2").exists())

    def test_account_form_rejects_another_users_property(self):
        cabin = Property.objects.create(owner=self.bob, name="Cabin", ownership="rented", street_address="1 Lake Rd", city="Kingston", province="ON", postal_code="K7L 1A1")
        self.client.force_login(self.alice)
        response = self.client.post(reverse("account_add"), {**ACCOUNT, "property": cabin.pk, "provider": self.hydro.pk, "account_number": "NEW-3"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillAccount.objects.filter(account_number="NEW-3").exists())

    def test_mark_paid(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("payment_mark_paid", args=[self.payment.pk]))
        self.assertRedirects(response, reverse("payment_list"))
        self.payment.refresh_from_db()
        self.assertEqual((self.payment.amount_paid, self.payment.status, self.payment.payment_date), (Decimal("100.00"), "paid", self.today))

    def test_mark_paid_requires_post(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("payment_mark_paid", args=[self.payment.pk])).status_code, 405)

    def test_mark_paid_only_follows_local_next(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("payment_mark_paid", args=[self.payment.pk]), {"next": "/reminders/"})
        self.assertRedirects(response, "/reminders/", fetch_redirect_response=False)
        other = self.bill(1, 5)
        response = self.client.post(reverse("payment_mark_paid", args=[other.pk]), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("payment_list"))
        other.refresh_from_db()
        self.assertEqual(other.status, "paid")

    def test_status_is_derived_from_amounts(self):
        self.client.force_login(self.alice)
        self.client.post(reverse("payment_add"), {**PAYMENT, "bill_account": self.account.pk})
        payment = BillPayment.objects.get(billing_year=2021)
        self.assertEqual(payment.status, "partially_paid")
        self.client.post(reverse("payment_edit", args=[payment.pk]), {**PAYMENT, "bill_account": self.account.pk, "amount_paid": "50.00"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")

    def test_status_filters_use_due_date(self):
        self.client.force_login(self.alice)
        overdue, upcoming, paid = self.bill(1, -5), self.bill(2, 5), self.bill(3, -5, paid="40.00")
        for status, expected in [("overdue", overdue), ("unpaid", upcoming), ("paid", paid)]:
            response = self.client.get(reverse("payment_list"), {"status": status, "year": "2020"})
            self.assertEqual(list(response.context["payments"]), [expected], status)

    def test_bad_filter_values_are_ignored(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("payment_list"), {"month": "abc", "year": "x", "provider": "zz", "property": "?"})
        self.assertEqual(response.status_code, 200)

    def test_bill_import_view(self):
        self.client.force_login(self.alice)
        with mock.patch("tracker.bill_import.extract_text", return_value=HYDRO_TEXT):
            response = self.client.post(reverse("bill_import"), {"files": [SimpleUploadedFile("jul.pdf", b"%PDF-1.4 x", content_type="application/pdf")], "property": ""})
        imported = BillPayment.objects.get(statement_date__isnull=False)
        self.assertRedirects(response, reverse("payment_edit", args=[imported.pk]))
        self.assertEqual(imported.amount_due, Decimal("99.40"))
        self.assertEqual(imported.bill_account.property, self.home)
        self.assertEqual(imported.attachments.get().original_name, "jul.pdf")

    def test_bill_import_reports_unreadable_files(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("bill_import"), {"files": [SimpleUploadedFile("scan.pdf", b"not really a pdf")], "property": ""}, follow=True)
        self.assertRedirects(response, reverse("bill_import"))
        self.assertContains(response, "isn&#x27;t a PDF")
        self.assertEqual(BillPayment.objects.count(), 1)

    def test_bill_import_rejects_non_pdf(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("bill_import"), {"files": [SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")], "property": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BillPayment.objects.count(), 1)

    def test_deleting_payment_removes_attachment_file(self):
        self.client.force_login(self.alice)
        attachment = Attachment.objects.create(payment=self.payment, file=SimpleUploadedFile("bill.pdf", b"%PDF-1.4 x"), original_name="bill.pdf")
        path = Path(attachment.file.path)
        self.assertTrue(path.exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("payment_delete", args=[self.payment.pk]))
        self.assertFalse(BillPayment.objects.filter(pk=self.payment.pk).exists())
        self.assertFalse(path.exists())

    def test_dashboard_property_summary(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("dashboard"))
        summary = response.context["properties"][0]
        self.assertEqual((summary["property"], summary["accounts"], summary["open_balance"]), (self.home, 1, Decimal("100.00")))
        self.assertContains(response, "Maple")


class HealthAndProxyTests(TestCase):
    def test_health_needs_no_login(self):
        response = self.client.get("/health/")
        self.assertEqual((response.status_code, response.content), (200, b"ok\n"))

    def test_health_reports_a_broken_database(self):
        with mock.patch("django.db.backends.utils.CursorWrapper.execute", side_effect=Exception("down")):
            self.assertEqual(self.client.get("/health/").status_code, 503)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_behind_the_tunnel(self):
        self.assertEqual(self.client.get("/login/").status_code, 301)  # plain HTTP is sent to HTTPS
        self.assertEqual(self.client.get("/login/", headers={"X-Forwarded-Proto": "https"}).status_code, 200)
        self.assertEqual(self.client.get("/health/").status_code, 200)  # local health checks skip the redirect
