import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tracker.models import BillAccount, BillPayment, PaymentCard, Property, Provider

class CardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.alice = User.objects.create_user("alice", password="x-safe-pass-123")
        cls.bob = User.objects.create_user("bob", password="x-safe-pass-456")
        water = Provider.objects.create(name="Town Water", category="water", is_default=True, is_custom=False)
        home = Property.objects.create(owner=cls.alice, name="Maple", ownership="owned", street_address="12 Maple St", city="Ottawa", province="ON", postal_code="K1A 0A1")
        cls.account = BillAccount.objects.create(owner=cls.alice, provider=water, property=home, account_number="W-1", service_address="12 Maple St",
                                                 city="Ottawa", province="ON", postal_code="K1A 0A1", due_day=20)
        cls.td = PaymentCard.objects.create(owner=cls.alice, issuer="td", name="TD Aeroplan Visa")
        cls.amex = PaymentCard.objects.create(owner=cls.alice, issuer="amex", name="Amex Cobalt")
        cls.bobs = PaymentCard.objects.create(owner=cls.bob, issuer="rbc", name="Bob's RBC")

    def setUp(self):
        self.client.force_login(self.alice)

    def bill(self, month, amount="50.00", paid="0", **extra):
        return BillPayment.objects.create(owner=self.alice, bill_account=self.account, billing_month=month, billing_year=2026,
                                          amount_due=Decimal(amount), amount_paid=Decimal(paid), due_date=date(2026, month, 20), **extra)

    def mark_paid(self, payment, **data):
        return self.client.post(reverse("payment_mark_paid", args=[payment.pk]), {"payment_date": "2026-10-03", **data})

    # Adding cards
    def test_add_a_card_from_the_list_or_a_custom_one(self):
        self.client.post(reverse("card_add"), {"issuer": "scotiabank", "name": "", "active": "on"})
        self.assertTrue(PaymentCard.objects.filter(owner=self.alice, name="Scotiabank", issuer="scotiabank").exists())  # company name by default
        self.client.post(reverse("card_add"), {"issuer": "other", "name": "Costco Mastercard", "active": "on"})
        self.assertTrue(PaymentCard.objects.filter(owner=self.alice, name="Costco Mastercard", issuer="other").exists())
        response = self.client.post(reverse("card_add"), {"issuer": "other", "name": "", "active": "on"})
        self.assertContains(response, "Give a custom card a name")

    def test_card_numbers_are_refused(self):
        for name in ["4520 1234 5678 9012", "4520-1234-5678-9012", "Visa 4520123456789012"]:
            response = self.client.post(reverse("card_add"), {"issuer": "td", "name": name, "active": "on"})
            self.assertContains(response, "looks like a card number", msg_prefix=name)
        self.assertFalse(PaymentCard.objects.filter(name__contains="4520").exists())
        self.client.post(reverse("card_add"), {"issuer": "td", "name": "TD Visa ending 9012", "active": "on"})  # last 4 digits are fine
        self.assertTrue(PaymentCard.objects.filter(name="TD Visa ending 9012").exists())

    def test_duplicate_names_are_refused_but_other_users_can_reuse_them(self):
        response = self.client.post(reverse("card_add"), {"issuer": "td", "name": "td aeroplan visa", "active": "on"})
        self.assertContains(response, "already have a card with this name")
        self.client.force_login(self.bob)
        self.client.post(reverse("card_add"), {"issuer": "td", "name": "TD Aeroplan Visa", "active": "on"})
        self.assertTrue(PaymentCard.objects.filter(owner=self.bob, name="TD Aeroplan Visa").exists())

    # Marking paid
    def test_mark_paid_with_a_card_records_card_method_and_date(self):
        july, august = self.bill(7), self.bill(8)
        response = self.mark_paid(august, paid_with=f"card:{self.td.pk}")
        self.assertRedirects(response, reverse("payment_list"))
        for p in (july, august):  # the earlier bill was carried into August's total, so it was paid with the same card
            p.refresh_from_db()
            self.assertEqual((p.status, p.card, p.payment_method, p.payment_date), ("paid", self.td, "credit_card", date(2026, 10, 3)))

    def test_mark_paid_another_way_or_not_recorded(self):
        a, b = self.bill(5), self.bill(6, paid="50.00")
        b.refresh_from_db()
        self.mark_paid(a, paid_with="method:bank_transfer")
        a.refresh_from_db()
        self.assertEqual((a.status, a.payment_method, a.card), ("paid", "bank_transfer", None))
        c = BillPayment.objects.create(owner=self.alice, bill_account=self.account, billing_month=9, billing_year=2026, amount_due=Decimal("10"),
                                       due_date=date(2026, 9, 20), payment_method="debit")
        self.mark_paid(c, paid_with="")
        c.refresh_from_db()
        self.assertEqual((c.status, c.payment_method), ("paid", "debit"))  # "Not recorded" leaves what was there

    def test_cannot_pay_with_someone_elses_or_a_hidden_card(self):
        PaymentCard.objects.filter(pk=self.amex.pk).update(active=False)
        payment = self.bill(7)
        for card in (self.bobs, self.amex):
            response = self.mark_paid(payment, paid_with=f"card:{card.pk}")
            self.assertEqual(response.status_code, 200)  # form shown again with an error
            payment.refresh_from_db()
            self.assertEqual((payment.status, payment.card), ("unpaid", None))

    def test_page_suggests_the_card_this_account_was_last_paid_with(self):
        self.bill(6, paid="50.00", card=self.amex, payment_date=date(2026, 6, 25))
        payment = self.bill(7)
        response = self.client.get(reverse("payment_mark_paid", args=[payment.pk]))
        self.assertEqual(response.context["form"].initial["paid_with"], f"card:{self.amex.pk}")
        self.assertContains(response, "TD Aeroplan Visa")
        self.assertNotContains(response, "Bob's RBC")

    # Payment form, lists, reports
    def test_payment_form_offers_only_your_active_cards_and_sets_the_method(self):
        payment = self.bill(7)
        form = self.client.get(reverse("payment_edit", args=[payment.pk])).context["form"]
        self.assertEqual(set(form.fields["card"].queryset), {self.td, self.amex})
        data = {"bill_account": self.account.pk, "billing_month": 7, "billing_year": 2026, "statement_date": "", "amount_due": "50.00",
                "due_date": "2026-07-20", "amount_paid": "50.00", "payment_date": "2026-07-18", "payment_method": "", "card": self.td.pk,
                "reference_number": "", "period_start": "", "period_end": "", "usage": "", "usage_unit": "", "notes": ""}
        self.client.post(reverse("payment_edit", args=[payment.pk]), data)
        payment.refresh_from_db()
        self.assertEqual((payment.card, payment.payment_method), (self.td, "credit_card"))
        response = self.client.post(reverse("payment_edit", args=[payment.pk]), {**data, "card": self.bobs.pk})
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.card, self.td)

    def test_list_filter_csv_and_report_show_the_card(self):
        self.bill(5, paid="50.00", card=self.td, payment_date=date(2026, 5, 18))
        self.bill(6, paid="70.00", amount="70.00", card=self.amex, payment_date=date(2026, 6, 18))
        listing = self.client.get(reverse("payment_list"), {"card": self.td.pk})
        self.assertEqual([p.card for p in listing.context["payments"]], [self.td])
        rows = list(csv.reader(io.StringIO(self.client.get(reverse("export_payments")).content.decode())))
        self.assertIn("Card", rows[0])
        self.assertEqual({r[rows[0].index("Card")] for r in rows[1:]}, {"TD Aeroplan Visa", "Amex Cobalt"})
        bars = self.client.get(reverse("reports")).context["card_bars"]
        self.assertEqual([b["label"] for b in bars], ["Amex Cobalt", "TD Aeroplan Visa"])

    # Removing cards
    def test_a_card_that_paid_bills_is_kept_but_can_be_hidden(self):
        self.bill(5, paid="50.00", card=self.td)
        self.client.post(reverse("card_delete", args=[self.td.pk]))
        self.assertTrue(PaymentCard.objects.filter(pk=self.td.pk).exists())
        self.client.post(reverse("card_edit", args=[self.td.pk]), {"issuer": "td", "name": "TD Aeroplan Visa"})  # "active" unticked
        self.assertFalse(PaymentCard.objects.get(pk=self.td.pk).active)
        self.client.post(reverse("card_delete", args=[self.amex.pk]))
        self.assertFalse(PaymentCard.objects.filter(pk=self.amex.pk).exists())

    def test_cards_are_private(self):
        self.assertEqual(self.client.get(reverse("card_edit", args=[self.bobs.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("card_delete", args=[self.bobs.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("card_list")), "Bob's RBC")

    def test_deleting_a_user_also_removes_their_cards(self):
        hydro = Provider.objects.create(name="City Hydro", category="electricity", is_default=True, is_custom=False)
        account = BillAccount.objects.create(owner=self.bob, provider=hydro, account_number="B-1", service_address="2 Main St",
                                             city="Ottawa", province="ON", postal_code="K1A 0A1", due_day=1)
        BillPayment.objects.create(owner=self.bob, bill_account=account, billing_month=1, billing_year=2026, amount_due=Decimal("5"),
                                   amount_paid=Decimal("5"), due_date=date(2026, 1, 1), card=self.bobs)
        self.bob.delete()  # RESTRICT only blocks deleting a card on its own, not with its owner
        self.assertFalse(PaymentCard.objects.filter(name="Bob's RBC").exists())
