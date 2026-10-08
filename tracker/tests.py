from datetime import date
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from .models import Attachment, BillAccount, BillPayment, Provider

class TrackerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="safe-password-123")
        cls.other = User.objects.create_user("bob", password="safe-password-456")
        cls.default = Provider.objects.create(name="Hydro Test", category="electricity", is_default=True, is_custom=False)
        cls.custom = Provider.objects.create(owner=cls.user, name="Local Co-op", category="other", is_custom=True)
        cls.account = BillAccount.objects.create(owner=cls.user, provider=cls.default, account_number="ACC-123", service_address="1 Main St", city="Toronto", province="ON", postal_code="M1M 1M1", typical_amount=Decimal("100"), due_day=15)
        cls.payment = BillPayment.objects.create(owner=cls.user, bill_account=cls.account, billing_month=7, billing_year=2026, amount_due=Decimal("100"), amount_paid=Decimal("80"), due_date=date(2026, 7, 15), status="partially_paid")

    def test_protected_pages_redirect_anonymous_users(self):
        for name in ["dashboard", "provider_list", "account_list", "payment_list", "reports", "reminders", "settings"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_authenticated_pages_and_exports_render(self):
        self.client.force_login(self.user)
        for name in ["dashboard", "provider_list", "account_list", "payment_list", "reports", "reminders", "settings", "export_accounts", "export_payments"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_user_cannot_edit_seeded_or_other_users_provider(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("provider_edit", args=[self.default.pk])).status_code, 404)
        other_provider = Provider.objects.create(owner=self.other, name="Private Provider", category="other")
        self.assertEqual(self.client.get(reverse("provider_edit", args=[other_provider.pk])).status_code, 404)

    def test_user_cannot_access_another_users_payment(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("payment_edit", args=[self.payment.pk])).status_code, 404)

    def test_custom_provider_is_immediately_available_to_account_form(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("account_add"))
        self.assertContains(response, "Local Co-op")
        self.assertContains(response, "Hydro Test")

    def test_account_and_payment_can_be_created_with_custom_provider(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("account_add"), {"provider": self.custom.pk, "account_number": "CUSTOM-1", "email": "", "service_address": "2 Main St", "city": "Toronto", "province": "ON", "postal_code": "M2M 2M2", "country": "Canada", "billing_frequency": "monthly", "typical_amount": "44.50", "due_day": "10", "active": "on", "notes": ""})
        self.assertRedirects(response, reverse("account_list"))
        account = BillAccount.objects.get(account_number="CUSTOM-1")
        self.assertEqual(account.owner, self.user)
        response = self.client.post(reverse("payment_add"), {"bill_account": account.pk, "billing_month": "8", "billing_year": "2026", "amount_due": "44.50", "amount_paid": "44.50", "payment_date": "2026-08-08", "due_date": "2026-08-10", "status": "paid", "payment_method": "bank_transfer", "reference_number": "REF-1", "notes": ""})
        self.assertRedirects(response, reverse("payment_list"))
        self.assertTrue(BillPayment.objects.filter(owner=self.user, bill_account=account, billing_month=8).exists())

    def test_account_form_rejects_other_users_custom_provider(self):
        private = Provider.objects.create(owner=self.other, name="Bob Only", category="other")
        self.client.force_login(self.user)
        response = self.client.post(reverse("account_add"), {"provider": private.pk, "account_number": "BAD", "service_address": "1 X", "city": "Toronto", "province": "ON", "postal_code": "M1M 1M1", "country": "Canada", "billing_frequency": "monthly", "typical_amount": "1", "due_day": "1", "active": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillAccount.objects.filter(account_number="BAD").exists())

    def test_filtered_csv_contains_only_matching_payment(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("export_payments"), {"year": 2026, "provider": self.default.pk})
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(b"Hydro Test", response.content)

    def test_attachment_download_is_owner_scoped(self):
        attachment = Attachment.objects.create(payment=self.payment, file=SimpleUploadedFile("bill.pdf", b"pdf-content", content_type="application/pdf"), original_name="bill.pdf")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("attachment_download", args=[attachment.pk])).status_code, 404)
        self.client.force_login(self.user)
        response = self.client.get(reverse("attachment_download", args=[attachment.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()
