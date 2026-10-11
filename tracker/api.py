"""Read-only bill list for another app of yours (Memento sends bill reminders from it).

GET /api/bills/ with "Authorization: Bearer <key>" (make a key with `manage.py create_api_token <user>`).
Returns the owner's unpaid bills plus bills due in the last 90 days, so a reminder can stop once a bill is paid.
Account numbers are left out: the provider, property and amounts are enough for a reminder.
"""
from datetime import timedelta
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import ApiToken, BillPayment

RECENT_DAYS = 90

def _token(request):
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not key.strip(): return None
    return ApiToken.objects.select_related("owner").filter(key_hash=ApiToken.hash(key.strip()), owner__is_active=True).first()

def _bill(request, p):
    a = p.bill_account
    return {"id": p.id, "provider": a.provider.name, "category": a.provider.category,
            "property": a.property.name if a.property else "", "period": p.period_label,
            "amount_due": str(p.amount_due), "amount_paid": str(p.amount_paid), "balance": str(p.balance),
            "due_date": p.due_date.isoformat(), "status": p.effective_status, "auto_pay": a.auto_pay,
            "payment_date": p.payment_date.isoformat() if p.payment_date else None, "needs_review": p.needs_review,
            "url": request.build_absolute_uri(reverse("payment_edit", args=[p.id]))}

@require_GET
def bills(request):
    token = _token(request)
    if token is None:
        return JsonResponse({"error": "A valid read-only key is required."}, status=401, headers={"WWW-Authenticate": "Bearer"})
    today = timezone.localdate()
    ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    qs = (BillPayment.objects.filter(owner=token.owner).filter(~Q(status="paid") | Q(due_date__gte=today - timedelta(days=RECENT_DAYS)))
          .select_related("bill_account__provider", "bill_account__property").order_by("due_date", "id"))
    response = JsonResponse({"today": today.isoformat(), "bills": [_bill(request, p) for p in qs]})
    response["Cache-Control"] = "no-store"
    return response
