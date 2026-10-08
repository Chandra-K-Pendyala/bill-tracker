from django.contrib import admin
from .models import Provider, BillAccount, BillPayment, Attachment
admin.site.register([Provider, BillAccount, BillPayment, Attachment])

