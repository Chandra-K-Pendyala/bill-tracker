from django import forms
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from .models import MONTHS, Attachment, BillAccount, BillPayment, Property, Provider

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BILL_FILES = 24

class DateInput(forms.DateInput): input_type = "date"

class ProviderForm(forms.ModelForm):
    class Meta:
        model = Provider
        fields = ["name", "category", "website_url", "phone_number", "province_region", "notes", "active"]
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user: self.instance.owner = user
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicate = Provider.objects.filter(owner=self.user, name__iexact=name)
        if self.instance.pk: duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists(): raise forms.ValidationError("You already have a custom provider with this name.")
        return name

class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ["name", "ownership", "street_address", "city", "province", "postal_code", "active", "notes"]
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user: self.instance.owner = user
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicate = Property.objects.filter(owner=self.user, name__iexact=name)
        if self.instance.pk: duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists(): raise forms.ValidationError("You already have a property with this name.")
        return name
    def clean_postal_code(self): return self.cleaned_data["postal_code"].strip().upper()

class BillAccountForm(forms.ModelForm):
    class Meta:
        model = BillAccount
        fields = ["property", "provider", "account_number", "email", "service_address", "city", "province", "postal_code",
                  "country", "billing_frequency", "typical_amount", "due_day", "auto_pay", "active", "notes"]
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user: self.instance.owner = user
        self.fields["provider"].queryset = Provider.objects.filter(active=True).filter(
            Q(owner__isnull=True) | Q(owner=user)).order_by("is_custom", "name")
        self.fields["property"].queryset = Property.objects.filter(owner=user)
    def save(self, commit=True):
        obj = super().save(False)
        obj.owner = self.user
        if commit: obj.save()
        return obj

class BillPaymentForm(forms.ModelForm):
    attachment = forms.FileField(required=False, validators=[FileExtensionValidator(["pdf", "png", "jpg", "jpeg", "webp"])],
                                 help_text="Optional PDF or image, maximum 10 MB.")
    class Meta:
        model = BillPayment
        fields = ["bill_account", "billing_month", "billing_year", "statement_date", "amount_due", "due_date",
                  "amount_paid", "payment_date", "payment_method", "reference_number", "period_start", "period_end",
                  "usage", "usage_unit", "notes"]
        widgets = {"billing_month": forms.Select(choices=MONTHS), "statement_date": DateInput(), "payment_date": DateInput(),
                   "due_date": DateInput(), "period_start": DateInput(), "period_end": DateInput()}
        labels = {"billing_month": "Billing month", "amount_due": "Amount due", "usage": "Usage"}
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user: self.instance.owner = user
        accounts = BillAccount.objects.filter(owner=user).select_related("provider", "property")
        if self.instance.pk: accounts = accounts.filter(Q(active=True) | Q(pk=self.instance.bill_account_id))
        else: accounts = accounts.filter(active=True)
        self.fields["bill_account"].queryset = accounts
        self.fields["amount_paid"].help_text = "Status is worked out from the amounts: paid, partially paid, or unpaid."
    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f and f.size > MAX_UPLOAD_BYTES: raise forms.ValidationError("File must be 10 MB or smaller.")
        return f
    def save(self, commit=True):
        obj = super().save(False)
        obj.owner = self.user
        obj.needs_review = False  # saving the form means the bill was looked over
        if commit:
            obj.save()
            f = self.cleaned_data.get("attachment")
            if f: Attachment.objects.create(payment=obj, file=f, original_name=f.name[:255])
        return obj

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "application/pdf,.pdf"}))
        super().__init__(*args, **kwargs)
    def clean(self, data, initial=None):
        files = [f for f in (data if isinstance(data, (list, tuple)) else [data]) if f] or [None]  # None → "required"
        return [super(MultipleFileField, self).clean(f, initial) for f in files]

class BillImportForm(forms.Form):
    files = MultipleFileField(label="Bill PDFs", validators=[FileExtensionValidator(["pdf"])],
                              help_text="Up to 24 files, 10 MB each.")
    property = forms.ModelChoiceField(queryset=Property.objects.none(), required=False, empty_label="Match by service address",
                                      help_text="Used for a new account only when its service address doesn't match one of your properties.")
    provider = forms.ModelChoiceField(queryset=Provider.objects.none(), required=False, empty_label="Recognize automatically",
                                      label="Read unrecognized bills as",
                                      help_text="Usually not needed: the company is read from the bill. Choose one only if a bill's company can't be found.")
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = Property.objects.filter(owner=user)
        self.fields["provider"].queryset = Provider.objects.filter(active=True).filter(Q(owner__isnull=True) | Q(owner=user)).order_by("name")
    def clean_files(self):
        files = self.cleaned_data["files"]
        if len(files) > MAX_BILL_FILES: raise forms.ValidationError(f"Upload at most {MAX_BILL_FILES} files at a time.")
        for f in files:
            if f.size > MAX_UPLOAD_BYTES: raise forms.ValidationError(f"{f.name} is larger than 10 MB.")
        return files

class ProfileForm(forms.Form):
    email = forms.EmailField(required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
