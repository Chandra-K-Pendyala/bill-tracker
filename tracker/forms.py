from django import forms
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from .models import Attachment, BillAccount, BillPayment, Provider

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

class BillAccountForm(forms.ModelForm):
    class Meta:
        model = BillAccount
        fields = ["provider", "account_number", "email", "service_address", "city", "province", "postal_code",
                  "country", "billing_frequency", "typical_amount", "due_day", "auto_pay", "active", "notes"]
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user: self.instance.owner = user
        self.fields["provider"].queryset = Provider.objects.filter(active=True).filter(
            Q(owner__isnull=True) | Q(owner=user)).order_by("is_custom", "name")
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
        fields = ["bill_account", "billing_month", "billing_year", "amount_due", "amount_paid", "payment_date",
                  "due_date", "status", "payment_method", "reference_number", "notes"]
        widgets = {"payment_date": DateInput(), "due_date": DateInput()}
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user: self.instance.owner = user
        self.fields["bill_account"].queryset = BillAccount.objects.filter(owner=user, active=True)
    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f and f.size > 10 * 1024 * 1024: raise forms.ValidationError("File must be 10 MB or smaller.")
        return f
    def save(self, commit=True):
        obj = super().save(False)
        obj.owner = self.user
        if commit:
            obj.save()
            f = self.cleaned_data.get("attachment")
            if f: Attachment.objects.create(payment=obj, file=f, original_name=f.name[:255])
        return obj

class ProfileForm(forms.Form):
    email = forms.EmailField(required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
