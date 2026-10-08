"""Major Ontario bill providers: seeded as built-in providers and recognized by the general bill reader.

Each entry is (name, category, region, pattern). The pattern is matched case-insensitively against the
text of a bill; the provider whose pattern appears earliest in the bill wins. Providers with their own
reader in bill_import.py (Hydro One, Toronto Hydro, Enbridge Gas, North Grenville, Wyse) are listed too,
so they are seeded, but their own readers run first.
"""
ONTARIO_PROVIDERS = [
    # Electricity distributors
    ("Hydro One", "electricity", "ON", r"hydro\s*one"),
    ("Toronto Hydro", "electricity", "ON", r"toronto\s*hydro|torontohydro\.com"),
    ("Alectra Utilities", "electricity", "ON", r"alectra"),
    ("Hydro Ottawa", "electricity", "ON", r"hydro\s*ottawa|hydroottawa\.com"),
    ("Elexicon Energy", "electricity", "ON", r"elexicon"),
    ("London Hydro", "electricity", "ON", r"london\s*hydro|londonhydro\.com"),
    ("Enova Power", "electricity", "ON", r"enova\s*power|enovapower\.com"),
    ("Oakville Hydro", "electricity", "ON", r"oakville\s*hydro"),
    ("Burlington Hydro", "electricity", "ON", r"burlington\s*hydro"),
    ("Milton Hydro", "electricity", "ON", r"milton\s*hydro"),
    ("Halton Hills Hydro", "electricity", "ON", r"halton\s*hills\s*hydro"),
    ("Newmarket-Tay Power", "electricity", "ON", r"newmarket[\s-]*tay"),
    ("ENWIN Utilities", "electricity", "ON", r"\benwin\b"),
    ("Entegrus", "electricity", "ON", r"entegrus"),
    ("ERTH Power", "electricity", "ON", r"\berth\s*power"),
    ("Essex Powerlines", "electricity", "ON", r"essex\s*powerlines"),
    ("GrandBridge Energy", "electricity", "ON", r"grandbridge"),
    ("Synergy North", "electricity", "ON", r"synergy\s*north"),
    ("Greater Sudbury Hydro", "electricity", "ON", r"greater\s*sudbury\s*hydro"),
    ("Utilities Kingston", "electricity", "ON", r"utilities\s*kingston|utilitieskingston\.com"),
    ("Oshawa Power", "electricity", "ON", r"oshawa\s*power"),
    ("Niagara Peninsula Energy", "electricity", "ON", r"niagara\s*peninsula\s*energy"),
    ("Bluewater Power", "electricity", "ON", r"bluewater\s*power"),
    ("Canadian Niagara Power", "electricity", "ON", r"canadian\s*niagara\s*power"),
    ("InnPower", "electricity", "ON", r"innpower"),
    ("Festival Hydro", "electricity", "ON", r"festival\s*hydro"),
    # Natural gas
    ("Enbridge Gas", "natural_gas", "Canada", r"enbridge"),
    ("Kitchener Utilities", "natural_gas", "ON", r"kitchener\s*utilities"),
    # Water and municipal utilities
    ("City of Toronto", "water", "ON", r"city\s+of\s+toronto"),
    ("Region of Peel", "water", "ON", r"region\s+of\s+peel|peelregion\.ca"),
    ("City of Mississauga", "water", "ON", r"city\s+of\s+mississauga"),
    ("City of Brampton", "water", "ON", r"city\s+of\s+brampton"),
    ("City of Ottawa", "water", "ON", r"city\s+of\s+ottawa"),
    ("City of Markham", "water", "ON", r"city\s+of\s+markham"),
    ("City of Vaughan", "water", "ON", r"city\s+of\s+vaughan"),
    ("City of Richmond Hill", "water", "ON", r"city\s+of\s+richmond\s+hill"),
    ("Regional Municipality of Durham", "water", "ON", r"(?:regional\s+municipality|region)\s+of\s+durham"),
    ("City of Barrie", "water", "ON", r"city\s+of\s+barrie"),
    ("City of Guelph", "water", "ON", r"city\s+of\s+guelph"),
    ("Municipality of North Grenville", "water", "ON", r"municipality\s+of\s+north\s+grenville"),
    # Sub-metering (condos and rentals)
    ("Wyse Meter Solutions", "water", "ON", r"wyse\s*meter|wysemeter\.com"),
    ("Metergy Solutions", "electricity", "ON", r"metergy"),
    ("Stratacon", "electricity", "ON", r"stratacon"),
    ("Carma Industries", "electricity", "ON", r"carma\s+industries|carmacorp"),
    # Phone, internet and TV
    ("Bell", "internet", "Canada", r"bell\s+canada|bell\s+mobility|\bbell\.ca\b"),
    ("Rogers", "internet", "Canada", r"rogers\.com|rogers\s+communications"),
    ("Telus", "mobile", "Canada", r"\btelus\b"),
    ("Fido", "mobile", "Canada", r"\bfido\b"),
    ("Koodo", "mobile", "Canada", r"\bkoodo\b"),
    ("Virgin Plus", "mobile", "Canada", r"virgin\s*plus"),
    ("Freedom Mobile", "mobile", "Canada", r"freedom\s*mobile"),
    ("Cogeco", "internet", "ON, QC", r"\bcogeco\b"),
    ("TekSavvy", "internet", "Canada", r"teksavvy"),
    # Water heater and HVAC rentals
    ("Enercare", "other", "ON", r"\benercare\b"),
    ("Reliance Home Comfort", "other", "ON", r"reliance\s+home\s+comfort"),
]
