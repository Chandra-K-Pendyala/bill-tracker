import csv
from datetime import timedelta
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.db.models import Q, Sum, Avg, Max
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import BillAccountForm, BillPaymentForm, ProfileForm, ProviderForm
from .models import Attachment, BillAccount, BillPayment, Provider, CATEGORIES

ZERO = Decimal("0")

def _payment_filters(request, qs):
    params = request.GET
    if params.get("month"): qs = qs.filter(billing_month=params["month"])
    if params.get("year"): qs = qs.filter(billing_year=params["year"])
    if params.get("provider"): qs = qs.filter(bill_account__provider_id=params["provider"])
    if params.get("category"): qs = qs.filter(bill_account__provider__category=params["category"])
    if params.get("status"): qs = qs.filter(status=params["status"])
    if params.get("province"): qs = qs.filter(bill_account__province=params["province"])
    if params.get("address"): qs = qs.filter(bill_account__service_address__icontains=params["address"])
    return qs

@login_required
def dashboard(request):
    today = timezone.localdate()
    payments = BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider")
    month = payments.filter(billing_year=today.year, billing_month=today.month)
    year = payments.filter(billing_year=today.year)
    unpaid = month.exclude(status="paid")
    upcoming = payments.exclude(status="paid").filter(due_date__gte=today, due_date__lte=today + timedelta(days=30))
    overdue = payments.exclude(status="paid").filter(due_date__lt=today)
    monthly = []
    for m in range(1, 13):
        monthly.append({"label": timezone.datetime(2000, m, 1).strftime("%b"),
                        "value": float(year.filter(billing_month=m).aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"])})
    context = {
        "today": today, "month_spend": month.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"],
        "year_spend": year.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"],
        "unpaid_total": unpaid.aggregate(v=Coalesce(Sum("amount_due"), ZERO) - Coalesce(Sum("amount_paid"), ZERO))["v"],
        "upcoming": upcoming[:8], "overdue": overdue[:8], "active_accounts": BillAccount.objects.filter(owner=request.user, active=True).count(),
        "category_data": list(year.values("bill_account__provider__category").annotate(total=Sum("amount_paid")).order_by("-total")),
        "provider_data": list(year.values("bill_account__provider__name").annotate(total=Sum("amount_paid")).order_by("-total")[:8]),
        "paid_count": month.filter(status="paid").count(), "unpaid_count": unpaid.count(), "monthly_data": monthly,
    }
    return render(request, "tracker/dashboard.html", context)

@login_required
def provider_list(request):
    qs = Provider.objects.filter(Q(owner__isnull=True) | Q(owner=request.user))
    q = request.GET.get("q", "").strip()
    if q: qs = qs.filter(Q(name__icontains=q) | Q(category__icontains=q) | Q(province_region__icontains=q))
    return render(request, "tracker/provider_list.html", {"providers": qs, "q": q})

@login_required
def provider_form(request, pk=None):
    obj = get_object_or_404(Provider, pk=pk, owner=request.user) if pk else None
    form = ProviderForm(request.POST or None, instance=obj, user=request.user)
    if request.method == "POST" and form.is_valid():
        provider = form.save(False); provider.owner = request.user; provider.is_custom = True; provider.is_default = False; provider.save()
        messages.success(request, "Custom provider saved."); return redirect("provider_list")
    return render(request, "tracker/form.html", {"form": form, "title": "Edit custom provider" if obj else "Add custom provider", "cancel_url": reverse("provider_list")})

@login_required
@require_POST
def provider_delete(request, pk):
    obj = get_object_or_404(Provider, pk=pk, owner=request.user)
    try: obj.delete(); messages.success(request, "Custom provider deleted.")
    except Exception: messages.error(request, "This provider is in use and cannot be deleted.")
    return redirect("provider_list")

@login_required
def account_list(request):
    qs = BillAccount.objects.filter(owner=request.user).select_related("provider")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(provider__name__icontains=q) | Q(account_number__icontains=q) | Q(email__icontains=q) |
                       Q(service_address__icontains=q) | Q(postal_code__icontains=q) | Q(provider__category__icontains=q))
    return render(request, "tracker/account_list.html", {"accounts": qs, "q": q})

@login_required
def account_form(request, pk=None):
    obj = get_object_or_404(BillAccount, pk=pk, owner=request.user) if pk else None
    form = BillAccountForm(request.POST or None, instance=obj, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Bill account saved."); return redirect("account_list")
    return render(request, "tracker/form.html", {"form": form, "title": "Edit bill account" if obj else "Add bill account", "cancel_url": reverse("account_list"), "provider_hint": True})

@login_required
@require_POST
def account_delete(request, pk):
    get_object_or_404(BillAccount, pk=pk, owner=request.user).delete()
    messages.success(request, "Bill account and its payment history were deleted.")
    return redirect("account_list")

@login_required
def payment_list(request):
    qs = BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider")
    qs = _payment_filters(request, qs)
    return render(request, "tracker/payment_list.html", {"payments": qs, **_filter_options(request.user)})

@login_required
def payment_form(request, pk=None):
    obj = get_object_or_404(BillPayment, pk=pk, owner=request.user) if pk else None
    form = BillPaymentForm(request.POST or None, request.FILES or None, instance=obj, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Payment record saved."); return redirect("payment_list")
    return render(request, "tracker/form.html", {"form": form, "title": "Edit payment" if obj else "Add monthly payment", "cancel_url": reverse("payment_list"), "attachments": obj.attachments.all() if obj else []})

@login_required
def attachment_download(request, pk):
    attachment = get_object_or_404(Attachment.objects.select_related("payment"), pk=pk, payment__owner=request.user)
    return FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name)

@login_required
@require_POST
def payment_delete(request, pk):
    get_object_or_404(BillPayment, pk=pk, owner=request.user).delete(); messages.success(request, "Payment deleted.")
    return redirect("payment_list")

def _filter_options(user):
    return {"providers": Provider.objects.filter(Q(owner__isnull=True) | Q(owner=user)), "categories": CATEGORIES,
            "years": range(timezone.localdate().year + 1, timezone.localdate().year - 10, -1)}

@login_required
def reports(request):
    today = timezone.localdate()
    qs = _payment_filters(request, BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider"))
    total = qs.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"]
    by_month = list(qs.values("billing_year", "billing_month").annotate(total=Sum("amount_paid")).order_by("billing_year", "billing_month"))
    this_month = BillPayment.objects.filter(owner=request.user, billing_year=today.year, billing_month=today.month)
    prev_date = (today.replace(day=1) - timedelta(days=1))
    prev = BillPayment.objects.filter(owner=request.user, billing_year=prev_date.year, billing_month=prev_date.month)
    curr_total = this_month.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"]
    prev_total = prev.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"]
    mom = ((curr_total - prev_total) / prev_total * 100) if prev_total else None
    context = {**_filter_options(request.user), "payments": qs[:100], "total": total,
        "average": qs.values("billing_year", "billing_month").annotate(t=Sum("amount_paid")).aggregate(v=Coalesce(Avg("t"), ZERO))["v"],
        "highest": qs.order_by("-amount_due").first(), "mom": mom,
        "overdue_total": qs.exclude(status="paid").filter(due_date__lt=today).aggregate(v=Coalesce(Sum("amount_due"), ZERO) - Coalesce(Sum("amount_paid"), ZERO))["v"],
        "upcoming_total": qs.exclude(status="paid").filter(due_date__gte=today, due_date__lte=today+timedelta(days=30)).aggregate(v=Coalesce(Sum("amount_due"), ZERO) - Coalesce(Sum("amount_paid"), ZERO))["v"],
        "category_data": list(qs.values("bill_account__provider__category").annotate(total=Sum("amount_paid")).order_by("-total")),
        "provider_data": list(qs.values("bill_account__provider__name").annotate(total=Sum("amount_paid")).order_by("-total")[:12]), "monthly_data": by_month}
    return render(request, "tracker/reports.html", context)

@login_required
def reminders(request):
    today = timezone.localdate(); qs = BillPayment.objects.filter(owner=request.user).exclude(status="paid").select_related("bill_account__provider")
    return render(request, "tracker/reminders.html", {"week": qs.filter(due_date__range=(today, today+timedelta(days=7))),
        "month": qs.filter(due_date__range=(today, today+timedelta(days=30))), "overdue": qs.filter(due_date__lt=today)})

@login_required
def export_accounts(request):
    response = HttpResponse(content_type="text/csv", headers={"Content-Disposition": 'attachment; filename="bill-accounts.csv"'})
    w = csv.writer(response); w.writerow(["Provider", "Category", "Account number", "Email", "Address", "City", "Province", "Postal code", "Frequency", "Typical amount", "Active"])
    for a in BillAccount.objects.filter(owner=request.user).select_related("provider"):
        w.writerow([a.provider.name, a.provider.get_category_display(), a.account_number, a.email, a.service_address, a.city, a.province, a.postal_code, a.get_billing_frequency_display(), a.typical_amount, a.active])
    return response

@login_required
def export_payments(request):
    qs = _payment_filters(request, BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider"))
    response = HttpResponse(content_type="text/csv", headers={"Content-Disposition": 'attachment; filename="payment-history.csv"'})
    w = csv.writer(response); w.writerow(["Provider", "Account", "Billing month", "Billing year", "Amount due", "Amount paid", "Due date", "Payment date", "Status", "Method", "Reference", "Category", "Province", "Address"])
    for p in qs:
        a=p.bill_account; w.writerow([a.provider.name, a.account_number, p.billing_month, p.billing_year, p.amount_due, p.amount_paid, p.due_date, p.payment_date or "", p.effective_status, p.get_payment_method_display(), p.reference_number, a.provider.get_category_display(), a.province, a.service_address])
    return response

@login_required
def settings_view(request):
    profile = ProfileForm(request.POST or None, initial={"email": request.user.email, "first_name": request.user.first_name, "last_name": request.user.last_name}, prefix="profile")
    password = PasswordChangeForm(request.user, request.POST or None, prefix="password")
    if request.method == "POST":
        if "save_profile" in request.POST and profile.is_valid():
            for f in ("email", "first_name", "last_name"): setattr(request.user, f, profile.cleaned_data[f])
            request.user.save(); messages.success(request, "Profile updated."); return redirect("settings")
        if "change_password" in request.POST and password.is_valid():
            user=password.save(); update_session_auth_hash(request, user); messages.success(request, "Password changed."); return redirect("settings")
    return render(request, "tracker/settings.html", {"profile_form": profile, "password_form": password})
