from datetime import timedelta
from decimal import Decimal
from io import StringIO
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from tracker.models import ApiToken, BillAccount, BillPayment, Property, Provider

class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.alice = User.objects.create_user("alice", password="x-safe-pass-123")
        cls.bob = User.objects.create_user("bob", password="x-safe-pass-123")
        home = Property.objects.create(owner=cls.alice, name="Cabin", ownership="rented", street_address="1 Lake Rd", city="Kingston", province="ON", postal_code="K7L 1A1")
        gas = Provider.objects.create(owner=cls.alice, name="Lake Gas", category="natural_gas")
        cls.account = BillAccount.objects.create(owner=cls.alice, provider=gas, property=home, account_number="123456789", due_day=5,
                                                 service_address="1 Lake Rd", city="Kingston", province="ON", postal_code="K7L 1A1")
        def bill(month, due, paid="0"):
            return BillPayment.objects.create(owner=cls.alice, bill_account=cls.account, billing_month=month, billing_year=2026,
                                              amount_due=Decimal("80.00"), amount_paid=Decimal(paid), due_date=due)
        cls.open = bill(1, cls.today + timedelta(days=3))
        cls.old_unpaid = bill(2, cls.today - timedelta(days=200))
        cls.recent_paid = bill(3, cls.today - timedelta(days=10), paid="80.00")
        cls.old_paid = bill(4, cls.today - timedelta(days=200), paid="80.00")
        bob_account = BillAccount.objects.create(owner=cls.bob, provider=Provider.objects.create(owner=cls.bob, name="Bob Hydro", category="electricity"),
                                                 account_number="9", due_day=1, service_address="2 Main St", city="Ottawa", province="ON", postal_code="K1A 0A1")
        BillPayment.objects.create(owner=cls.bob, bill_account=bob_account, billing_month=1, billing_year=2026, amount_due=Decimal("5"), due_date=cls.today)

    def get(self, key):
        return self.client.get("/api/bills/", HTTP_AUTHORIZATION=f"Bearer {key}")

    def test_key_reads_only_its_owners_open_and_recent_bills(self):
        response = self.get(ApiToken.issue(self.alice, "memento"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        bills = response.json()["bills"]
        self.assertEqual([b["id"] for b in bills], [self.old_unpaid.id, self.recent_paid.id, self.open.id])
        first = next(b for b in bills if b["id"] == self.open.id)
        self.assertEqual((first["provider"], first["property"], first["balance"], first["status"], first["auto_pay"]),
                         ("Lake Gas", "Cabin", "80.00", "unpaid", False))
        self.assertNotIn("123456789", response.content.decode())  # no account numbers
        self.assertIsNotNone(ApiToken.objects.get(owner=self.alice).last_used_at)

    def test_missing_wrong_or_replaced_key_is_refused(self):
        self.assertEqual(self.client.get("/api/bills/").status_code, 401)
        self.assertEqual(self.get("bt_not-a-real-key").status_code, 401)
        old = ApiToken.issue(self.alice, "memento")
        new = ApiToken.issue(self.alice, "memento")
        self.assertEqual(self.get(old).status_code, 401)
        self.assertEqual(self.get(new).status_code, 200)
        self.assertEqual(ApiToken.objects.count(), 1)

    def test_key_of_inactive_user_is_refused_and_api_is_read_only(self):
        key = ApiToken.issue(self.alice, "memento")
        self.assertEqual(self.client.post("/api/bills/", HTTP_AUTHORIZATION=f"Bearer {key}").status_code, 405)
        User.objects.filter(pk=self.alice.pk).update(is_active=False)
        self.assertEqual(self.get(key).status_code, 401)

    def test_only_the_hash_is_stored(self):
        key = ApiToken.issue(self.alice, "memento")
        stored = ApiToken.objects.get(owner=self.alice).key_hash
        self.assertNotIn(key, stored)
        self.assertEqual(stored, ApiToken.hash(key))

    def test_create_api_token_command(self):
        out = StringIO()
        call_command("create_api_token", "alice", stdout=out)
        key = out.getvalue().strip().splitlines()[-1]
        self.assertTrue(key.startswith("bt_"))
        self.assertEqual(self.get(key).status_code, 200)
