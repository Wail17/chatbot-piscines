#!/usr/bin/env python
import json
import re
from typing import List, Dict, Any
from difflib import SequenceMatcher

FAQ_PATH = "app/data/all/faq/FAQAI.jsonl"

def normalize(s: str) -> str:
    """Normalize text for comparison"""
    s = s.lower().strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'[?!.,;]', '', s)
    return s

def similarity(a: str, b: str) -> float:
    """Calculate similarity between two strings"""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()

def find_matching_entry(faq_list: List[Dict], question: str) -> tuple:
    """Find best matching entry by question, return (index, score)"""
    best_idx = -1
    best_score = 0.0

    for idx, entry in enumerate(faq_list):
        existing_q = entry.get("Vraag", "")
        score = similarity(question, existing_q)
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx, best_score

def load_faq() -> List[Dict]:
    """Load FAQ from JSONL file"""
    faq_list = []
    try:
        with open(FAQ_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    faq_list.append(json.loads(line))
    except FileNotFoundError:
        print(f"File not found: {FAQ_PATH}")
    return faq_list

def save_faq(faq_list: List[Dict]):
    """Save FAQ to JSONL file"""
    with open(FAQ_PATH, 'w', encoding='utf-8') as f:
        for entry in faq_list:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

# FAQ entries to update/insert
faq_updates = [
    {
        "question": "Wat is fout E002 of fout E102 op de Beniferro frequentieregelaar?",
        "answer": """Fout E 002 of E102 komt meestal voor als de frequentieregelaar te zwaar belast wordt door een filterpomp-fout. Het maximale vermogen dat de frequentieregelaar aankan, is 1200 Watt, maar dat wordt soms (bij voorbeeld bij opstart of veroudering van kogelladers ...) overschreden. De fout kan ook bij sommige pompen voorkomen, als het toerental te laag wordt teruggeschroefd. Het is aan te raden, de frequentieregelaar op een andere pomp te testen.""",
        "category": "Frequentieregelaar",
        "gen1": "x", "gen2": "x", "gen3": "", "frequentieregelaar": "x"
    },
    {
        "question": "Ik krijg de foutmelding \"verkeerd passwoord\" als ik mijn wifipool apparaat probeer te koppelen",
        "answer": """U kunt de foutmelding "Wrong Password" (Verkeerd wachtwoord) krijgen als:
- Het wachtwoord verkeerd werd ingegeven
- Tijdens het koppelproces maakt uw telefoon even verbinding met het wifinetwerk van de wifipool printplaat. Daarna schakelt de app/telefoon terug naar het thuisnetwerk, maar op dat ogenblik kan de app/telefoon uw thuisnetwerk niet vinden.
De fout "Wrong password" betekent dus dat de app tijdelijk geen verbinding kan maken met het thuisnetwerk op het moment dat het wachtwoord wordt geverifieerd.

Wat kunt u doen?
- De printplaat heropstarten door de stekker uit en in te trekken.
- Indien het wachtwoord correct is ingegeven, wist u de laatste letter en probeert opnieuw te koppelen.
- Verander uw locatie (soms werkt koppelen beter op 10–15 meter afstand).
- Indien uw apparaat een ethernet-aansluiting heeft: sluit de kabel aan en probeer opnieuw.
- Controleer de veiligheidsinstellingen van uw router.
- Probeer later opnieuw (de server kan overbelast zijn).
- Onze systemen werken enkel op 2.4 GHz Wi-Fi (Wi-Fi 5 support, geen Wi-Fi 6). Let op dat u hiermee verbonden bent.""",
        "category": "Wifipool",
        "gen1": "x", "gen2": "x", "gen3": "", "wifipool": "x"
    },
    {
        "question": "De pH op mijn toestel wijkt sterk af en of varieert heel hard. Hoe kan dit?",
        "answer": """De pH kan op het toestel sterk afwijken en/of erg variëren. Dit wijst er meestal op dat de kalibratievloeistof vervuild of te oud is. Een vervuilde of oude vloeistof kan ervoor zorgen dat de meetwaarden onnauwkeurig worden, omdat de kalibratievloeistof dan niet meer de juiste pH-waarde heeft. Ook kan het zijn dat u de verkeerde kalibratievloeistof gebruikt (bv. een ander merk of type).

Een tweede mogelijkheid is dat de elektrode vervuild is. Dit gebeurt vaak door algen of vuil op het oppervlak. Reinig de elektrode zorgvuldig met een zachte doek en pH 4-reinigingsvloeistof (pH 4-oplossing), spoel daarna af met leidingwater en kalibreer opnieuw.

Een derde oorzaak is dat de elektrode is verouderd of aan vervanging toe is. Elektroden slijten na verloop van tijd (doorgaans na 1–2 jaar, afhankelijk van gebruik en onderhoud) en moeten dan worden vervangen.

Een vierde mogelijkheid is dat de fles kalibratievloeistof lang open heeft gestaan of meerdere keren is gebruikt. Dit kan leiden tot vervuiling of verdamping, waardoor de kalibratievloeistof niet meer de juiste pH-waarde heeft. Gebruik altijd verse kalibratievloeistof en zorg ervoor dat de fles goed is afgesloten.

Een vijfde factor is de temperatuur van het water. De temperatuur kan de pH-meting beïnvloeden, dus zorg ervoor dat uw toestel correct gekalibreerd is bij de juiste temperatuur (meestal 25°C voor de kalibratievloeistof).

Als laatste kan het ook zijn dat het zwembadwater zelf sterk verandert (bijvoorbeeld door toevoeging van chemicaliën, regenwater of veel zwemmers), wat zorgt voor grote schommelingen in de pH-waarde. Controleer ook of de watertemperatuur stabiel blijft en of er geen andere factoren zijn die de pH kunnen beïnvloeden.

Als u na deze stappen nog steeds problemen ondervindt, neem dan contact op met de technische dienst voor verdere diagnose.""",
        "category": "pH Meting",
        "gen1": "x", "gen2": "x", "gen3": "", "wifipool": "x", "display": "x"
    },
    {
        "question": "Wifipool gen 2 Reset",
        "answer": """Om een Wifipool Gen 2 te resetten, volgt u deze stappen:

1. Schakel het toestel uit door de stekker uit het stopcontact te halen.
2. Wacht 10 seconden.
3. Druk de resetknop in (meestal aan de zijkant of achterkant van het toestel) en houd deze ingedrukt.
4. Steek de stekker weer in het stopcontact terwijl u de resetknop ingedrukt houdt.
5. Houd de resetknop nog 10 seconden ingedrukt.
6. Laat de resetknop los. Het toestel start nu op in fabrieksinstellingen.

Het LED-lampje knippert snel tijdens het resetten en brandt daarna continu als het toestel opnieuw is opgestart.

Na de reset moet u het toestel opnieuw koppelen met uw Wi-Fi-netwerk via de app.""",
        "category": "Wifipool",
        "gen1": "", "gen2": "x", "gen3": "", "wifipool": "x"
    },
    {
        "question": "De pH waarde van mijn apparaat komt niet overeen met de kleurmeting. Wat kan ik hieraan doen?",
        "answer": """Als de pH-waarde van uw apparaat niet overeenkomt met de kleurmeting, kan dit verschillende oorzaken hebben:

1. **Kalibratie nodig**: Het apparaat moet regelmatig worden gekalibreerd met verse kalibratievloeistof (pH 7,0 en pH 4,0). Volg de kalibratie-instructies in de handleiding.

2. **Vervuilde elektrode**: Reinig de pH-elektrode met een zachte doek en pH 4-reinigingsvloeistof. Spoel daarna af met leidingwater en kalibreer opnieuw.

3. **Verouderde elektrode**: pH-elektroden hebben een beperkte levensduur (1–2 jaar). Als de elektrode oud is, moet deze worden vervangen.

4. **Vervuilde of oude kalibratievloeistof**: Gebruik altijd verse kalibratievloeistof uit een nieuwe, goed afgesloten fles.

5. **Verkeerde kleurmeting**: Zorg ervoor dat u de kleurtest correct uitvoert. Gebruik verse testvloeistof en vergelijk de kleur in goed licht. Let op: kleurmetingen kunnen subjectief zijn.

6. **Temperatuurverschillen**: De pH-meting is temperatuurafhankelijk. Zorg ervoor dat het water en de kalibratievloeistof ongeveer dezelfde temperatuur hebben.

7. **Waterchemie**: Hoge chloorconcentraties of andere chemicaliën kunnen de pH-meting beïnvloeden. Test het water opnieuw nadat u het chloor hebt laten zakken.

Als het probleem aanhoudt na deze stappen, neem dan contact op met de technische dienst.""",
        "category": "pH Meting",
        "gen1": "x", "gen2": "x", "gen3": "", "wifipool": "x", "display": "x"
    },
    {
        "question": "Hoe schakel ik mijn wifipool toestel manueel aan?",
        "answer": """Om uw Wifipool-toestel manueel te bedienen:

**Via de app:**
1. Open de Wifipool-app op uw smartphone.
2. Selecteer uw toestel.
3. Ga naar het tabblad "Handmatig" of "Manual".
4. Hier kunt u de pomp, verlichting, verwarming en andere functies handmatig in- en uitschakelen.

**Via het apparaat zelf (Gen 1 met display):**
1. Druk op de menutoets op het display.
2. Navigeer naar "Manueel" of "Manual".
3. Selecteer de functie die u wilt bedienen (bijv. pomp, verlichting).
4. Schakel de functie in of uit met de +/- toetsen.

**Belangrijk:**
- In manuele modus worden de automatische schema's tijdelijk uitgeschakeld.
- Schakel terug naar automatische modus om de normale planning te hervatten.
- Gen 2-apparaten zonder display kunnen alleen via de app worden bediend.""",
        "category": "Wifipool",
        "gen1": "x", "gen2": "x", "gen3": "", "wifipool": "x"
    },
    {
        "question": "Mijn HS Zoutelektrolyse geeft E2 fout. Wat is dat?",
        "answer": """Ofwel is de watertemperatuur te laag (onder 15 graden) ofwel te hoog (boven 40 graden).
Als ook fout E7 verschijnt, is mogelijk de temperatuursensor defect en moet deze vervangen worden.""",
        "category": "Zoutelektrolyse",
        "gen1": "x", "gen2": "x", "gen3": "", "zoutelektrolyse": "x"
    },
    {
        "question": "Hoe knippert het ledje op de Wifipool Gen 2 printplaten?",
        "answer": """Gen 2 printplaat:
- Knippert ~2× per seconde: niet gekoppeld, signaal uitzenden
- Knippert zeer snel: programmatiefout
- Continu branden: gekoppeld
- Continu branden: niet geprogrammeerd met Beniferro/Zwemcloud software
- Uit: geen contact""",
        "category": "Wifipool",
        "gen1": "", "gen2": "x", "gen3": "", "wifipool": "x"
    }
]

def main():
    print("Loading FAQ...")
    faq_list = load_faq()
    print(f"Loaded {len(faq_list)} entries")

    updated_count = 0
    added_count = 0

    for update in faq_updates:
        question = update["question"]
        answer = update["answer"]
        category = update.get("category", "Algemeen")

        # Find matching entry
        idx, score = find_matching_entry(faq_list, question)

        # Update or add entry
        if idx >= 0 and score > 0.7:  # High similarity threshold
            print(f"\nUpdating: {question[:60]}... (similarity: {score:.2f})")
            faq_list[idx]["Vraag"] = question
            faq_list[idx]["Antwoord"] = answer
            faq_list[idx]["Categorie"] = category

            # Update flags
            for key in ["gen1", "gen2", "gen3", "wifipool", "display", "frequentieregelaar", "zoutelektrolyse"]:
                if key in update:
                    field_name = key.capitalize() if key in ["gen1", "gen2", "gen3"] else key.replace("_", "").title()
                    if key == "gen1":
                        field_name = "Gen1"
                    elif key == "gen2":
                        field_name = "Gen2"
                    elif key == "gen3":
                        field_name = "Gen3"
                    elif key == "wifipool":
                        field_name = "Wifipool"
                    elif key == "frequentieregelaar":
                        field_name = "Frequentieregelaar"
                    elif key == "zoutelektrolyse":
                        field_name = "Zoutelektrolyse"
                    elif key == "display":
                        field_name = "Display"
                    faq_list[idx][field_name] = update[key]

            updated_count += 1
        else:
            print(f"\nAdding new: {question[:60]}...")
            new_entry = {
                "Categorie": category,
                "Vraag": question,
                "Antwoord": answer,
                "Foto": "",
                "Filmpje": "",
                "Gen1": update.get("gen1", ""),
                "Gen2": update.get("gen2", ""),
                "Gen3": update.get("gen3", ""),
                "Wifipool": update.get("wifipool", ""),
                "Display": update.get("display", ""),
                "VloeibareChloor": "",
                "Zoutelektrolyse": update.get("zoutelektrolyse", ""),
                "EPDM": "",
                "AutKranen": "",
                "Frequentieregelaar": update.get("frequentieregelaar", ""),
                "DEFrage": "",
                "DEAntwort": "",
                "FRQuestion": "",
                "FRReponse": "",
                "ENQuestion": "",
                "ENAnswer": ""
            }
            faq_list.append(new_entry)
            added_count += 1

    print(f"\n\nSaving FAQ...")
    save_faq(faq_list)
    print(f"Done! Updated: {updated_count}, Added: {added_count}")
    print(f"Total entries: {len(faq_list)}")

if __name__ == "__main__":
    main()
