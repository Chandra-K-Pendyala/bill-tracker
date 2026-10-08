from django.contrib import admin
from .models import Provider, Property, BillAccount, BillPayment, Attachment
admin.site.register([Provider, Property, BillAccount, BillPayment, Attachment])
