from django.urls import path
from . import views
urlpatterns = [
 path("", views.dashboard, name="dashboard"),
 path("providers/", views.provider_list, name="provider_list"), path("providers/new/", views.provider_form, name="provider_add"),
 path("providers/<int:pk>/edit/", views.provider_form, name="provider_edit"), path("providers/<int:pk>/delete/", views.provider_delete, name="provider_delete"),
 path("accounts/", views.account_list, name="account_list"), path("accounts/new/", views.account_form, name="account_add"),
 path("accounts/<int:pk>/edit/", views.account_form, name="account_edit"), path("accounts/<int:pk>/delete/", views.account_delete, name="account_delete"),
 path("payments/", views.payment_list, name="payment_list"), path("payments/new/", views.payment_form, name="payment_add"),
 path("payments/<int:pk>/edit/", views.payment_form, name="payment_edit"), path("payments/<int:pk>/delete/", views.payment_delete, name="payment_delete"),
 path("attachments/<int:pk>/download/", views.attachment_download, name="attachment_download"),
 path("reports/", views.reports, name="reports"), path("reminders/", views.reminders, name="reminders"),
 path("exports/accounts.csv", views.export_accounts, name="export_accounts"), path("exports/payments.csv", views.export_payments, name="export_payments"),
 path("settings/", views.settings_view, name="settings"),
]
