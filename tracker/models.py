from pathlib import Path
import uuid
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

CATEGORIES = [
    ("electricity", "Electricity"), ("natural_gas", "Natural gas"), ("water", "Water"),
    ("internet", "Internet"), ("mobile", "Mobile phone"), ("home_phone", "Home phone"),
    ("tv", "TV/cable"), ("insurance", "Insurance"), ("property_tax", "Property tax"),
    ("mortgage_rent", "Mortgage/rent"), ("condo_fees", "Condo/maintenance fees"),
    ("credit_card", "Credit cards"), ("loan", "Loans"), ("subscription", "Subscriptions"),
    ("other", "Other"),
]
CATEGORY_LABELS = dict(CATEGORIES)
PROVINCES = [(x, x) for x in ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"]]
MONTHS = [(m, timezone.datetime(2000, m, 1).strftime("%B")) for m in range(1, 13)]
ADDRESS_FIELDS = {"service_address": "street_address", "city": "city", "province": "province", "postal_code": "postal_code"}
STATUS_FILTERS = {"paid": "Paid", "unpaid": "Unpaid", "partially_paid": "Partially paid", "overdue": "Overdue"}

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

class Property(models.Model):
    OWNERSHIP = [("owned", "Owned"), ("rented", "Rented")]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="properties")
    name = models.CharField(max_length=100, help_text="A short name, such as the street address or “Cottage”.")
    ownership = models.CharField(max_length=10, choices=OWNERSHIP)
    street_address = models.CharField(max_length=255, help_text="Include the unit, for example “52 De Grassi St, Unit 1”.")
    city = models.CharField(max_length=100)
    province = models.CharField(max_length=2, choices=PROVINCES)
    postal_code = models.CharField(max_length=7)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="unique_property_owner_name")]
    def __str__(self): return self.name

class BillAccount(models.Model):
    FREQUENCIES = [(x, x.replace("_", " ").title()) for x in ["monthly", "bi_monthly", "quarterly", "yearly", "custom"]]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bill_accounts")
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="accounts")
    property = models.ForeignKey(Property, null=True, blank=True, on_delete=models.PROTECT, related_name="accounts",
                                 help_text="The address fields below are filled from the property when left blank.")
    account_number = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    service_address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=2, choices=PROVINCES, blank=True)
    postal_code = models.CharField(max_length=7, blank=True)
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
        if self.property_id:
            if self.property.owner_id != self.owner_id:
                raise ValidationError({"property": "Select one of your properties."})
            for field, source in ADDRESS_FIELDS.items():
                if not getattr(self, field): setattr(self, field, getattr(self.property, source))
        missing = [field for field in ADDRESS_FIELDS if not getattr(self, field)]
        if missing: raise ValidationError({field: "Required unless you choose a property." for field in missing})
    def __str__(self): return f"{self.provider.name} — {self.account_number}"

class BillPayment(models.Model):
    STATUSES = [("unpaid", "Unpaid"), ("partially_paid", "Partially paid"), ("paid", "Paid")]
    METHODS = [(x, x.replace("_", " ").title()) for x in ["bank_transfer", "credit_card", "debit", "auto_pay", "cash", "cheque", "other"]]
    USAGE_UNITS = [("kWh", "kWh"), ("m³", "m³")]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    bill_account = models.ForeignKey(BillAccount, on_delete=models.CASCADE, related_name="payments")
    billing_month = models.PositiveSmallIntegerField()
    billing_year = models.PositiveSmallIntegerField()
    statement_date = models.DateField(null=True, blank=True, help_text="The date printed on the bill.")
    period_start = models.DateField(null=True, blank=True, help_text="First day of the service period.")
    period_end = models.DateField(null=True, blank=True, help_text="Last day of the service period.")
    amount_due = models.DecimalField(max_digits=12, decimal_places=2, help_text="New charges on this bill.")
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_date = models.DateField(null=True, blank=True)
    due_date = models.DateField()
    # Derived from the amounts on every save; "overdue" is computed from the due date (effective_status).
    status = models.CharField(max_length=20, choices=STATUSES, default="unpaid", editable=False)
    payment_method = models.CharField(max_length=20, choices=METHODS, blank=True)
    reference_number = models.CharField(max_length=120, blank=True)
    usage = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Consumption shown on the bill.")
    usage_unit = models.CharField(max_length=10, choices=USAGE_UNITS, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-billing_year", "-billing_month", "due_date"]
        constraints = [models.UniqueConstraint(fields=["bill_account", "billing_month", "billing_year"], name="unique_account_billing_period")]
    def clean(self):
        errors = {}
        if self.billing_month is not None and not 1 <= self.billing_month <= 12: errors["billing_month"] = "Enter a month from 1 to 12."
        if self.billing_year is not None and not 2000 <= self.billing_year <= 2100: errors["billing_year"] = "Enter a year from 2000 to 2100."
        if self.amount_paid is not None and self.amount_paid < 0: errors["amount_paid"] = "The amount paid cannot be negative."
        if self.usage is not None and self.usage < 0: errors["usage"] = "Usage cannot be negative."
        if self.period_start and self.period_end and self.period_end < self.period_start:
            errors["period_end"] = "The period cannot end before it starts."
        if self.bill_account_id and self.bill_account.owner_id != self.owner_id:
            errors["bill_account"] = "Select one of your bill accounts."
        if errors: raise ValidationError(errors)
    @staticmethod
    def status_for(amount_due, amount_paid):
        if amount_due <= 0 or amount_paid >= amount_due: return "paid"
        return "partially_paid" if amount_paid > 0 else "unpaid"
    def save(self, *args, **kwargs):
        self.status = self.status_for(self.amount_due, self.amount_paid)
        if kwargs.get("update_fields") is not None: kwargs["update_fields"] = {*kwargs["update_fields"], "status"}
        super().save(*args, **kwargs)
    @property
    def balance(self): return max(self.amount_due - self.amount_paid, 0)
    @property
    def effective_status(self):
        if self.status != "paid" and self.due_date < timezone.localdate(): return "overdue"
        return self.status
    @property
    def effective_status_label(self): return STATUS_FILTERS[self.effective_status]
    @property
    def period_label(self): return f"{MONTHS[self.billing_month - 1][1][:3]} {self.billing_year}"
    def __str__(self): return f"{self.bill_account} — {self.billing_year}-{self.billing_month:02d}"

def attachment_path(instance, filename):
    ext = Path(filename).suffix.lower()[:10]
    return f"attachments/{instance.payment.owner_id}/{uuid.uuid4().hex}{ext}"

class Attachment(models.Model):
    payment = models.ForeignKey(BillPayment, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=attachment_path)
    original_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

@receiver(post_delete, sender=Attachment)
def delete_attachment_file(sender, instance, **kwargs):
    # Remove the stored file once the deletion commits (also runs for cascaded account/payment deletes).
    name, storage = instance.file.name, instance.file.storage
    if name: transaction.on_commit(lambda: storage.delete(name))
