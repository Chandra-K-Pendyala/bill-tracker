from pathlib import Path
import uuid
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

CATEGORIES = [
    ("electricity", "Electricity"), ("natural_gas", "Natural gas"), ("water", "Water"),
    ("internet", "Internet"), ("mobile", "Mobile phone"), ("home_phone", "Home phone"),
    ("tv", "TV/cable"), ("insurance", "Insurance"), ("property_tax", "Property tax"),
    ("mortgage_rent", "Mortgage/rent"), ("condo_fees", "Condo/maintenance fees"),
    ("credit_card", "Credit cards"), ("loan", "Loans"), ("subscription", "Subscriptions"),
    ("other", "Other"),
]
PROVINCES = [(x, x) for x in ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"]]

class Provider(models.Model):
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="providers")
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=30, choices=CATEGORIES)
    website_url = models.URLField(blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    province_region = models.CharField(max_length=100, blank=True)
    is_default = models.BooleanField(default=False, editable=False)
    is_custom = models.BooleanField(default=True, editable=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="unique_custom_provider_owner_name")]
    def __str__(self): return self.name

class BillAccount(models.Model):
    FREQUENCIES = [(x, x.replace("_", " ").title()) for x in ["monthly", "bi_monthly", "quarterly", "yearly", "custom"]]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bill_accounts")
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="accounts")
    account_number = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    service_address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    province = models.CharField(max_length=2, choices=PROVINCES)
    postal_code = models.CharField(max_length=7)
    country = models.CharField(max_length=80, default="Canada")
    billing_frequency = models.CharField(max_length=20, choices=FREQUENCIES, default="monthly")
    typical_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_day = models.PositiveSmallIntegerField(default=1)
    auto_pay = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ["provider__name", "account_number"]
    def clean(self):
        if not 1 <= self.due_day <= 31: raise ValidationError({"due_day": "Enter a day from 1 to 31."})
        if self.provider_id and self.provider.owner_id not in (None, self.owner_id):
            raise ValidationError({"provider": "Select a built-in provider or one you created."})
    def __str__(self): return f"{self.provider.name} — {self.account_number}"

class BillPayment(models.Model):
    STATUSES = [(x, x.replace("_", " ").title()) for x in ["unpaid", "partially_paid", "paid", "overdue"]]
    METHODS = [(x, x.replace("_", " ").title()) for x in ["bank_transfer", "credit_card", "debit", "auto_pay", "cash", "cheque", "other"]]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    bill_account = models.ForeignKey(BillAccount, on_delete=models.CASCADE, related_name="payments")
    billing_month = models.PositiveSmallIntegerField()
    billing_year = models.PositiveSmallIntegerField()
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_date = models.DateField(null=True, blank=True)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUSES, default="unpaid")
    payment_method = models.CharField(max_length=20, choices=METHODS, blank=True)
    reference_number = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-billing_year", "-billing_month", "due_date"]
        constraints = [models.UniqueConstraint(fields=["bill_account", "billing_month", "billing_year"], name="unique_account_billing_period")]
    def clean(self):
        if not 1 <= self.billing_month <= 12: raise ValidationError({"billing_month": "Enter a month from 1 to 12."})
        if self.bill_account_id and self.bill_account.owner_id != self.owner_id:
            raise ValidationError({"bill_account": "Select one of your bill accounts."})
    @property
    def effective_status(self):
        if self.status != "paid" and self.due_date < timezone.localdate(): return "overdue"
        return self.status
    def __str__(self): return f"{self.bill_account} — {self.billing_year}-{self.billing_month:02d}"

def attachment_path(instance, filename):
    ext = Path(filename).suffix.lower()[:10]
    return f"attachments/{instance.payment.owner_id}/{uuid.uuid4().hex}{ext}"

class Attachment(models.Model):
    payment = models.ForeignKey(BillPayment, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=attachment_path)
    original_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

