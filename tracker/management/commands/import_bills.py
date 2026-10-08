from contextlib import ExitStack
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from tracker.bill_import import BillImportError, extract_text, import_bills, match_property, parse_bill
from tracker.models import Property

class Command(BaseCommand):
    help = "Import Hydro One, Enbridge Gas and North Grenville water bill PDFs from files or folders (searched recursively)."
    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", help="PDF files or folders of PDFs.")
        parser.add_argument("--user", required=True, help="Username that owns the imported records.")
        parser.add_argument("--property", help="Property name to use for new accounts whose address doesn't match a property.")
        parser.add_argument("--dry-run", action="store_true", help="Read and show the bills without saving anything.")
    def handle(self, *args, **options):
        try: user = get_user_model().objects.get(username=options["user"])
        except get_user_model().DoesNotExist: raise CommandError(f"No user named {options['user']!r}.")
        prop = None
        if options["property"]:
            prop = Property.objects.filter(owner=user, name=options["property"]).first()
            if prop is None: raise CommandError(f"{options['user']} has no property named {options['property']!r}.")
        pdfs = []
        for raw in options["paths"]:
            path = Path(raw)
            if path.is_dir(): pdfs += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
            elif path.is_file(): pdfs.append(path)
            else: raise CommandError(f"{raw} doesn't exist.")
        if not pdfs: raise CommandError("No PDF files found.")
        with ExitStack() as stack:
            files = [(File(stack.enter_context(path.open("rb")), name=path.name), path.name) for path in pdfs]
            if options["dry_run"]: return self._preview(user, files)
            results = import_bills(user, files, property=prop)
        style = {"created": self.style.SUCCESS, "updated": self.style.SUCCESS, "duplicate": self.style.WARNING, "error": self.style.ERROR}
        for result in results: self.stdout.write(style[result.outcome](f"[{result.outcome}] {result.message}"))
        counts = {outcome: sum(r.outcome == outcome for r in results) for outcome in style}
        self.stdout.write(", ".join(f"{n} {outcome}" for outcome, n in counts.items()) + f" ({len(results)} files).")
        if counts["error"]: raise CommandError("Some bills were not imported.")
    def _preview(self, user, files):
        for f, name in files:
            try: bill = parse_bill(extract_text(f))
            except BillImportError as exc:
                self.stdout.write(self.style.ERROR(f"{name}: {exc}")); continue
            prop = match_property(user, bill.service_address, bill.postal_codes)[0]
            paid = ", ".join(f"${amount} on {day}" for day, amount in bill.payments) or "none listed"
            self.stdout.write(f"{name}: {bill.label} · account {bill.account_number} · {bill.service_address} → {prop or 'no matching property'}\n"
                              f"    statement {bill.statement_date} · due {bill.due_date} · ${bill.amount_due} · period {bill.period_start}–{bill.period_end}"
                              f" · usage {bill.usage} {bill.usage_unit} · earlier bills settled: {bill.previous_settled} · payments: {paid}")
