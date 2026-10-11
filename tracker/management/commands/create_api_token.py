from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from tracker.models import ApiToken

class Command(BaseCommand):
    help = "Make a read-only key that lets another app of yours (Memento) read a user's bills. Running it again replaces the key."
    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--name", default="memento", help="What the key is for (default: memento).")
    def handle(self, *args, **options):
        try: user = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist: raise CommandError(f"No user called '{options['username']}'.")
        key = ApiToken.issue(user, options["name"])
        self.stdout.write(f"Read-only key '{options['name']}' for {user.username} (shown once; any older key with this name stops working):")
        self.stdout.write(key)
