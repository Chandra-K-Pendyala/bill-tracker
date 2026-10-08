import csv
from datetime import timedelta
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from .bill_import import import_bills
from .forms import BillAccountForm, BillImportForm, BillPaymentForm, ProfileForm, PropertyForm, ProviderForm
from .models import CATEGORIES, CATEGORY_LABELS, MONTHS, STATUS_FILTERS, Attachment, BillAccount, BillPayment, Property, Provider

ZERO = Decimal("0")
OPEN_BALANCE = Coalesce(Sum(F("amount_due") - F("amount_paid")), ZERO)

def _payment_filters(request, qs):
    params, today = request.GET, timezone.localdate()
    for param, lookup in [("month", "billing_month"), ("year", "billing_year"), ("provider", "bill_account__provider_id"),
                          ("property", "bill_account__property_id")]:
        if params.get(param, "").isdigit(): qs = qs.filter(**{lookup: params[param]})
    if params.get("category"): qs = qs.filter(bill_account__provider__category=params["category"])
    status = params.get("status")
    if status == "paid": qs = qs.filter(status="paid")
    elif status == "overdue": qs = qs.exclude(status="paid").filter(due_date__lt=today)
    elif status in ("unpaid", "partially_paid"): qs = qs.filter(status=status, due_date__gte=today)
    if params.get("province"): qs = qs.filter(bill_account__province__iexact=params["province"].strip())
    if params.get("address"): qs = qs.filter(bill_account__service_address__icontains=params["address"])
    return qs

def _filter_options(user):
    return {"providers": Provider.objects.filter(Q(owner__isnull=True) | Q(owner=user)), "categories": CATEGORIES,
            "properties": Property.objects.filter(owner=user), "months": MONTHS, "status_choices": STATUS_FILTERS.items(),
            "years": range(timezone.localdate().year + 1, timezone.localdate().year - 10, -1)}

def _bars(rows, label_key, labels=None):
    """Rows of {label_key, total} as horizontal bars, widest first."""
    rows = [r for r in rows if r["total"]]
    top = max((r["total"] for r in rows), default=0)
    return [{"label": (labels or {}).get(r[label_key], r[label_key]) or "No property", "total": r["total"],
             "pct": round(r["total"] / top * 100) if top else 0} for r in rows]

def _breakdowns(qs):
    """Billed totals by category, provider and property for a payment queryset."""
    by = lambda key: list(qs.values(key).annotate(total=Sum("amount_due")).order_by("-total"))
    return {"category_bars": _bars(by("bill_account__provider__category"), "bill_account__provider__category", CATEGORY_LABELS),
            "provider_bars": _bars(by("bill_account__provider__name")[:10], "bill_account__provider__name"),
            "property_bars": _bars(by("bill_account__property__name"), "bill_account__property__name")}

def _property_summaries(user, today):
    stats = {row["bill_account__property"]: row for row in BillPayment.objects.filter(owner=user)
             .values("bill_account__property").annotate(
                 year_billed=Coalesce(Sum("amount_due", filter=Q(billing_year=today.year)), ZERO),
                 year_paid=Coalesce(Sum("amount_paid", filter=Q(billing_year=today.year)), ZERO),
                 open_balance=Coalesce(Sum(F("amount_due") - F("amount_paid"), filter=~Q(status="paid")), ZERO),
                 overdue=Count("pk", filter=~Q(status="paid") & Q(due_date__lt=today)))}
    summaries = []
    for prop in Property.objects.filter(owner=user).annotate(account_count=Count("accounts")):
        row = stats.get(prop.pk, {})
        summaries.append({"property": prop, "accounts": prop.account_count, **{k: row.get(k, ZERO) for k in ("year_billed", "year_paid", "open_balance")},
                          "overdue": row.get("overdue", 0)})
    return summaries

def _next_url(request, default):
    url = request.POST.get("next") or request.GET.get("next")
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()): return url
    return reverse(default)

@login_required
def dashboard(request):
    today = timezone.localdate()
    payments = BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider", "bill_account__property")
    month = payments.filter(billing_year=today.year, billing_month=today.month)
    year = payments.filter(billing_year=today.year)
    open_bills = payments.exclude(status="paid")
    billed = {row["billing_month"]: row["total"] for row in year.values("billing_month").annotate(total=Sum("amount_due")) if row["total"]}
    chart_months = range(min(billed), max(max(billed), today.month) + 1) if billed else []  # first billed month to now
    context = {
        "today": today, "month_paid": month.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"],
        "year_paid": year.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"],
        "open_total": open_bills.aggregate(v=OPEN_BALANCE)["v"], "open_count": open_bills.count(),
        "overdue_count": open_bills.filter(due_date__lt=today).count(),
        "upcoming": open_bills.filter(due_date__gte=today, due_date__lte=today + timedelta(days=30)).order_by("due_date")[:8],
        "overdue": open_bills.filter(due_date__lt=today).order_by("due_date")[:8],
        "active_accounts": BillAccount.objects.filter(owner=request.user, active=True).count(),
        "paid_count": month.filter(status="paid").count(), "unpaid_count": month.exclude(status="paid").count(),
        "monthly_data": [{"label": MONTHS[m - 1][1][:3], "value": float(billed.get(m, 0))} for m in chart_months],
        "properties": _property_summaries(request.user, today), **_breakdowns(year),
    }
    return render(request, "tracker/dashboard.html", context)

@login_required
def property_list(request):
    today = timezone.localdate()
    return render(request, "tracker/property_list.html", {"summaries": _property_summaries(request.user, today), "today": today})

@login_required
def property_form(request, pk=None):
    obj = get_object_or_404(Property, pk=pk, owner=request.user) if pk else None
    form = PropertyForm(request.POST or None, instance=obj, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Property saved."); return redirect("property_list")
    return render(request, "tracker/form.html", {"form": form, "title": "Edit property" if obj else "Add property", "cancel_url": reverse("property_list")})

@login_required
@require_POST
def property_delete(request, pk):
    obj = get_object_or_404(Property, pk=pk, owner=request.user)
    try: obj.delete(); messages.success(request, "Property deleted.")
    except ProtectedError: messages.error(request, "This property still has bill accounts. Move or delete them first.")
    return redirect("property_list")

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
    except ProtectedError: messages.error(request, "This provider is in use and cannot be deleted.")
    return redirect("provider_list")

@login_required
def account_list(request):
    qs = BillAccount.objects.filter(owner=request.user).select_related("provider", "property")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(provider__name__icontains=q) | Q(account_number__icontains=q) | Q(email__icontains=q) |
                       Q(service_address__icontains=q) | Q(postal_code__icontains=q) | Q(provider__category__icontains=q) |
                       Q(property__name__icontains=q))
    return render(request, "tracker/account_list.html", {"accounts": qs, "q": q})

@login_required
def account_detail(request, pk):
    account = get_object_or_404(BillAccount.objects.select_related("provider", "property"), pk=pk, owner=request.user)
    history = list(account.payments.order_by("billing_year", "billing_month").prefetch_related("attachments"))
    totals = account.payments.aggregate(billed=Coalesce(Sum("amount_due"), ZERO), paid=Coalesce(Sum("amount_paid"), ZERO),
                                        average=Coalesce(Avg("amount_due"), ZERO))
    context = {"account": account, "payments": history[::-1], **totals, "open_balance": sum((p.balance for p in history), ZERO),
               "amount_data": [{"label": p.period_label, "value": float(p.amount_due)} for p in history],
               "usage_data": [{"label": p.period_label, "value": float(p.usage)} for p in history if p.usage is not None],
               "usage_unit": next((p.usage_unit for p in reversed(history) if p.usage_unit), "")}
    return render(request, "tracker/account_detail.html", context)

@login_required
def account_form(request, pk=None):
    obj = get_object_or_404(BillAccount, pk=pk, owner=request.user) if pk else None
    form = BillAccountForm(request.POST or None, instance=obj, user=request.user)
    if request.method == "POST" and form.is_valid():
        account = form.save(); messages.success(request, "Bill account saved."); return redirect("account_detail", account.pk)
    return render(request, "tracker/form.html", {"form": form, "title": "Edit bill account" if obj else "Add bill account",
                                                 "cancel_url": reverse("account_list"), "provider_hint": True})

@login_required
@require_POST
def account_delete(request, pk):
    get_object_or_404(BillAccount, pk=pk, owner=request.user).delete()
    messages.success(request, "Bill account and its payment history were deleted.")
    return redirect("account_list")

@login_required
def payment_list(request):
    qs = BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider", "bill_account__property").prefetch_related("attachments")
    qs = _payment_filters(request, qs)
    return render(request, "tracker/payment_list.html", {"payments": qs, **_filter_options(request.user)})

@login_required
def payment_form(request, pk=None):
    obj = get_object_or_404(BillPayment, pk=pk, owner=request.user) if pk else None
    initial = {}
    if obj is None:
        today = timezone.localdate()
        initial = {"billing_month": today.month, "billing_year": today.year}
        account = BillAccount.objects.filter(owner=request.user, pk=request.GET.get("account", "")).first() if request.GET.get("account", "").isdigit() else None
        if account: initial.update(bill_account=account, amount_due=account.typical_amount)
    form = BillPaymentForm(request.POST or None, request.FILES or None, instance=obj, initial=initial, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "Payment record saved."); return redirect(_next_url(request, "payment_list"))
    return render(request, "tracker/form.html", {"form": form, "title": "Edit payment" if obj else "Add monthly payment", "cancel_url": _next_url(request, "payment_list"),
                                                 "attachments": obj.attachments.all() if obj else [], "payment": obj})

@login_required
@require_POST
def payment_mark_paid(request, pk):
    payment = get_object_or_404(BillPayment.objects.select_related("bill_account__provider"), pk=pk, owner=request.user)
    if payment.amount_paid < payment.amount_due:
        payment.amount_paid = payment.amount_due
        payment.payment_date = payment.payment_date or timezone.localdate()
        payment.save(update_fields=["amount_paid", "payment_date", "updated_at"])
        messages.success(request, f"Marked {payment.bill_account.provider.name} · {payment.period_label} as paid.")
    return redirect(_next_url(request, "payment_list"))

@login_required
def bill_import(request):
    form = BillImportForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        results = import_bills(request.user, [(f, f.name) for f in form.cleaned_data["files"]], property=form.cleaned_data["property"])
        levels = {"created": messages.SUCCESS, "updated": messages.SUCCESS, "duplicate": messages.INFO, "error": messages.ERROR}
        for result in results: messages.add_message(request, levels[result.outcome], result.message)
        imported = [r.payment for r in results if r.outcome in ("created", "updated")]
        if len(results) == 1 and imported: return redirect("payment_edit", imported[0].pk)
        return redirect("payment_list" if imported else "bill_import")
    return render(request, "tracker/import.html", {"form": form})

@login_required
def attachment_download(request, pk):
    attachment = get_object_or_404(Attachment.objects.select_related("payment"), pk=pk, payment__owner=request.user)
    return FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name)

@login_required
@require_POST
def payment_delete(request, pk):
    get_object_or_404(BillPayment, pk=pk, owner=request.user).delete(); messages.success(request, "Payment deleted.")
    return redirect(_next_url(request, "payment_list"))

@login_required
def reports(request):
    today = timezone.localdate()
    qs = _payment_filters(request, BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider"))
    by_month = list(qs.values("billing_year", "billing_month").annotate(total=Sum("amount_due"), paid=Sum("amount_paid")).order_by("billing_year", "billing_month"))
    this_month = BillPayment.objects.filter(owner=request.user, billing_year=today.year, billing_month=today.month)
    prev_date = (today.replace(day=1) - timedelta(days=1))
    prev = BillPayment.objects.filter(owner=request.user, billing_year=prev_date.year, billing_month=prev_date.month)
    curr_total = this_month.aggregate(v=Coalesce(Sum("amount_due"), ZERO))["v"]
    prev_total = prev.aggregate(v=Coalesce(Sum("amount_due"), ZERO))["v"]
    mom = ((curr_total - prev_total) / prev_total * 100) if prev_total else None
    open_qs = qs.exclude(status="paid")
    context = {**_filter_options(request.user), **_breakdowns(qs), "payments": qs[:100],
        "billed": qs.aggregate(v=Coalesce(Sum("amount_due"), ZERO))["v"], "total": qs.aggregate(v=Coalesce(Sum("amount_paid"), ZERO))["v"],
        "average": sum((row["total"] for row in by_month), ZERO) / len(by_month) if by_month else ZERO,
        "highest": qs.order_by("-amount_due").select_related("bill_account__provider").first(), "mom": mom,
        "overdue_total": open_qs.filter(due_date__lt=today).aggregate(v=OPEN_BALANCE)["v"],
        "upcoming_total": open_qs.filter(due_date__gte=today, due_date__lte=today + timedelta(days=30)).aggregate(v=OPEN_BALANCE)["v"],
        "monthly_data": [{"label": f"{MONTHS[r['billing_month'] - 1][1][:3]} {r['billing_year']}", "value": float(r["total"])} for r in by_month]}
    return render(request, "tracker/reports.html", context)

@login_required
def reminders(request):
    today = timezone.localdate()
    qs = BillPayment.objects.filter(owner=request.user).exclude(status="paid").select_related("bill_account__provider", "bill_account__property").order_by("due_date")
    return render(request, "tracker/reminders.html", {"week": qs.filter(due_date__range=(today, today + timedelta(days=7))),
        "month": qs.filter(due_date__range=(today + timedelta(days=8), today + timedelta(days=30))), "overdue": qs.filter(due_date__lt=today)})

@login_required
def export_accounts(request):
    response = HttpResponse(content_type="text/csv", headers={"Content-Disposition": 'attachment; filename="bill-accounts.csv"'})
    w = csv.writer(response); w.writerow(["Property", "Ownership", "Provider", "Category", "Account number", "Email", "Address", "City", "Province", "Postal code", "Frequency", "Typical amount", "Active"])
    for a in BillAccount.objects.filter(owner=request.user).select_related("provider", "property"):
        w.writerow([a.property.name if a.property else "", a.property.get_ownership_display() if a.property else "", a.provider.name, a.provider.get_category_display(),
                    a.account_number, a.email, a.service_address, a.city, a.province, a.postal_code, a.get_billing_frequency_display(), a.typical_amount, a.active])
    return response

@login_required
def export_payments(request):
    qs = _payment_filters(request, BillPayment.objects.filter(owner=request.user).select_related("bill_account__provider", "bill_account__property"))
    response = HttpResponse(content_type="text/csv", headers={"Content-Disposition": 'attachment; filename="payment-history.csv"'})
    w = csv.writer(response); w.writerow(["Property", "Provider", "Account", "Billing month", "Billing year", "Statement date", "Amount due", "Amount paid",
                                          "Due date", "Payment date", "Status", "Method", "Reference", "Usage", "Usage unit", "Category", "Province", "Address"])
    for p in qs:
        a = p.bill_account
        w.writerow([a.property.name if a.property else "", a.provider.name, a.account_number, p.billing_month, p.billing_year, p.statement_date or "",
                    p.amount_due, p.amount_paid, p.due_date, p.payment_date or "", p.effective_status, p.get_payment_method_display(), p.reference_number,
                    "" if p.usage is None else p.usage, p.usage_unit, a.provider.get_category_display(), a.province, a.service_address])
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
            user = password.save(); update_session_auth_hash(request, user); messages.success(request, "Password changed."); return redirect("settings")
    return render(request, "tracker/settings.html", {"profile_form": profile, "password_form": password})
