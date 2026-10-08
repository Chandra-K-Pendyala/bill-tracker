import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Create the initial admin from environment variables if it does not exist."
    def add_arguments(self, parser):
        parser.add_argument("--reset-password", action="store_true",
                            help="Also set an existing admin's password to ADMIN_PASSWORD.")
    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "").strip()
        password = os.getenv("ADMIN_PASSWORD", "")
        email = os.getenv("ADMIN_EMAIL", "")
        if not username or not password:
            self.stdout.write("ADMIN_USERNAME/ADMIN_PASSWORD not set; skipping admin creation."); return
        if password.startswith("replace-with"):
            self.stderr.write("ADMIN_PASSWORD is still the placeholder from .env.example; skipping admin creation."); return
        user, created = get_user_model().objects.get_or_create(username=username, defaults={"email": email, "is_staff": True, "is_superuser": True})
        if created:
            user.set_password(password); user.save(); self.stdout.write(self.style.SUCCESS(f"Created admin '{username}'."))
        elif options["reset_password"]:
            user.set_password(password); user.save(); self.stdout.write(self.style.SUCCESS(f"Reset the password for '{username}'."))
        else: self.stdout.write(f"Admin '{username}' already exists; password unchanged.")
