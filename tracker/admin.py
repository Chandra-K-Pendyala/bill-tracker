from django.contrib import admin
from .models import ApiToken, Provider, Property, BillAccount, BillPayment, Attachment
admin.site.register([Provider, Property, BillAccount, BillPayment, Attachment])

@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    # Keys are made with `manage.py create_api_token`; here they can only be seen and deleted (revoked).
    list_display = ["name", "owner", "created_at", "last_used_at"]
    readonly_fields = ["owner", "name", "created_at", "last_used_at"]
    def has_add_permission(self, request): return False
