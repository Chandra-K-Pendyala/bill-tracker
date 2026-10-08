from django.db import migrations

def derive_status(apps, schema_editor):
    """Status is now worked out from the amounts; "overdue" is no longer stored (it comes from the due date)."""
    BillPayment = apps.get_model("tracker", "BillPayment")
    for payment in BillPayment.objects.only("pk", "amount_due", "amount_paid", "status"):
        if payment.amount_due <= 0 or payment.amount_paid >= payment.amount_due: status = "paid"
        elif payment.amount_paid > 0: status = "partially_paid"
        else: status = "unpaid"
        if payment.status != status: BillPayment.objects.filter(pk=payment.pk).update(status=status)

class Migration(migrations.Migration):
    dependencies = [("tracker", "0002_properties_and_bill_details")]
    operations = [migrations.RunPython(derive_status, migrations.RunPython.noop)]
