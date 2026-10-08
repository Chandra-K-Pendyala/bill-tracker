from django.core.management.base import BaseCommand
from tracker.models import Provider

PROVIDERS = [
 ("Toronto Hydro","electricity","ON"),("Hydro One","electricity","ON"),("Alectra Utilities","electricity","ON"),
 ("Enbridge Gas","natural_gas","Canada"),("BC Hydro","electricity","BC"),("Hydro-Québec","electricity","QC"),
 ("Manitoba Hydro","electricity","MB"),("SaskPower","electricity","SK"),("EPCOR","electricity","AB"),
 ("FortisBC","natural_gas","BC"),("Nova Scotia Power","electricity","NS"),("NB Power","electricity","NB"),
 ("Newfoundland Power","electricity","NL"),("Bell","internet","Canada"),("Rogers","internet","Canada"),
 ("Telus","mobile","Canada"),("Fido","mobile","Canada"),("Koodo","mobile","Canada"),
 ("Virgin Plus","mobile","Canada"),("Freedom Mobile","mobile","Canada"),("Videotron","internet","QC"),
 ("Shaw","internet","Western Canada"),("Cogeco","internet","ON, QC"),("TekSavvy","internet","Canada"),
 ("City of Toronto","water","ON"),("Region of Peel","water","ON"),("York Region","water","ON"),
 ("City of Mississauga","water","ON"),("City of Brampton","water","ON"),("City of Ottawa","water","ON"),
 ("City of Calgary","water","AB"),("City of Edmonton","water","AB"),("City of Vancouver","water","BC"),
 ("City of Montreal","water","QC"),
]
class Command(BaseCommand):
    help = "Idempotently seed built-in Canadian service providers."
    def handle(self, *args, **kwargs):
        count=0
        for name, category, region in PROVIDERS:
            _, created = Provider.objects.update_or_create(owner=None, name=name, defaults={"category":category,"province_region":region,"is_default":True,"is_custom":False,"active":True})
            count += created
        self.stdout.write(self.style.SUCCESS(f"Provider seed complete ({count} created)."))

