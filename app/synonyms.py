# app/synonyms.py
"""
Multilingual synonym dictionary for pool/piscine domain.

Supports Dutch (NL), French (FR), English (EN) and common abbreviations.

Usage:
    from app.synonyms import normalize_with_synonyms, expand_with_synonyms, get_synonyms

Purpose:
    When a user asks question A using different words than question B,
    the synonym system ensures both map to the same canonical terms,
    so the retrieval finds the correct answer regardless of phrasing.

Example:
    "pH zuurtegraad te laag"  == "acidité trop basse"  == "pH too low"
    "kalibreer de sonde"      == "calibrate the probe"  == "calibrer le capteur"
    "wifi verbindingsprobleem" == "connection issue"    == "problème réseau"
"""

import re
import logging
from typing import Dict, List, Set, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)


# ============================================================================
# SYNONYM GROUPS
# Each group has:
#   - canonical: the normalized form to use internally
#   - terms: all synonym variants (NL, FR, EN, abbreviations, typos)
# ============================================================================

SYNONYM_GROUPS: List[Dict] = [

    # ── POOL CHEMISTRY ────────────────────────────────────────────────────────

    {
        "canonical": "ph",
        "category": "chemistry",
        "terms": [
            # NL
            "ph", "zuurtegraad", "zuurheid", "aciditeit", "basisch", "zuurgraad",
            "ph waarde", "ph niveau", "ph stand",
            # FR
            "acidite", "acidité", "ph niveau", "niveau ph", "valeur ph", "taux ph",
            # EN
            "acidity", "ph level", "ph value", "ph reading", "acid level",
            # Abbreviations
            "p.h.", "hydrogen",
        ],
    },

    {
        "canonical": "chloor",
        "category": "chemistry",
        "terms": [
            # NL
            "chloor", "chloortabletten", "chloorgranulaat", "chloorpoeder",
            "chloorconcentratie", "desinfectiemiddel", "ontsmettingsmiddel",
            "vrij chloor", "gebonden chloor", "restchloor",
            # FR
            "chlore", "chlore libre", "chlore combine", "chlore résiduel",
            "desinfectant", "désinfectant", "taux chlore",
            # EN
            "chlorine", "free chlorine", "combined chlorine", "residual chlorine",
            "bleach", "disinfectant", "chlorination",
            # Abbreviations
            "cl", "cl2",
        ],
    },

    {
        "canonical": "orp",
        "category": "chemistry",
        "terms": [
            # NL
            "orp", "redox", "redoxpotentiaal", "oxidatiepotentiaal",
            "rx waarde", "rx niveau", "rx meting",
            "oxidatie reductie potentiaal",
            # FR
            "potentiel redox", "potentiel d'oxydoréduction", "valeur redox",
            # EN
            "oxidation reduction potential", "redox potential", "orp value",
            "orp reading", "orp level",
            # Abbreviations
            "mv", "millivolt", "rx", "r.x.",
        ],
    },

    {
        "canonical": "zout",
        "category": "chemistry",
        "terms": [
            # NL
            "zout", "zoutgehalte", "zoutconcentratie", "natriumchloride",
            "zwembadzout", "electrolysezout", "zoutwaarde",
            # FR
            "sel", "teneur en sel", "concentration sel", "chlorure de sodium",
            # EN
            "salt", "salt level", "salt concentration", "sodium chloride", "nacl",
            # Abbreviations
            "nacl", "na", "ppm zout", "g/l zout",
        ],
    },

    {
        "canonical": "elektrolyse",
        "category": "chemistry",
        "terms": [
            # NL
            "elektrolyse", "zoutelektrolyse", "zoutchlorinatie", "electrolyse",
            "zout systeem", "zoutwater systeem", "zoutelektrolysetoestel",
            "elektrolysetoestel",
            # FR
            "electrolyse", "électrolyse", "electrolyse au sel", "chloration sel",
            "electrolyseur", "électrolyseur", "cellule d'electrolyse",
            # EN
            "electrolysis", "salt electrolysis", "salt chlorination",
            "saltwater system", "salt cell", "electrolyser", "electrolyzer",
            # DE
            "salzelektrolyse", "salzelectrolyse", "salzelektrolysegerät",
            "elektrolyse", "elektrolyseur",
            # Product names
            "electrolyzer", "elektrolyseur", "zoutcel", "cel",
        ],
    },

    {
        "canonical": "alkaliniteit",
        "category": "chemistry",
        "terms": [
            # NL
            "alkaliniteit", "totale alkaliniteit", "ta waarde", "ta niveau",
            "bicarbonaat", "buffercapaciteit",
            # FR
            "alcalinite", "alcalinité", "alcalinité totale", "ta",
            "titre alcalimetrique", "capacite tampon",
            # EN
            "alkalinity", "total alkalinity", "ta", "bicarbonate",
            "buffer capacity",
            # Abbreviations
            "ta", "th", "t.a.",
        ],
    },

    {
        "canonical": "hardheid",
        "category": "chemistry",
        "terms": [
            # NL
            "hardheid", "waterhardheid", "calcium hardheid", "th waarde",
            "kalk", "kalkgehalte",
            # FR
            "durete", "dureté", "dureté de l'eau", "titre hydrotimetrique",
            "calcaire", "calcium",
            # EN
            "hardness", "water hardness", "calcium hardness", "scale",
            "limescale",
            # Abbreviations
            "th", "t.h.", "ppm calcium",
        ],
    },

    # ── SENSORS & MEASUREMENTS ────────────────────────────────────────────────

    {
        "canonical": "sensor",
        "category": "sensor",
        "terms": [
            # NL
            "sensor", "sonde", "elektrode", "meetprobe", "probe",
            "ph sensor", "ph sonde", "ph elektrode",
            "orp sensor", "orp sonde", "orp elektrode",
            "temperatuursensor", "temperatuurmeter",
            "zoutsensor", "zoutmeter",
            # FR
            "capteur", "sonde de mesure", "électrode",
            "sonde ph", "capteur ph", "électrode ph",
            "sonde orp", "capteur redox",
            # EN
            "sensor", "probe", "electrode", "detector",
            "ph probe", "ph electrode", "ph sensor",
            "orp probe", "orp sensor",
            # Generic
            "meetapparaat", "meter",
        ],
    },

    {
        "canonical": "meting",
        "category": "measurement",
        "terms": [
            # NL
            "meting", "meten", "meetwaarde", "waarde", "aflezing",
            "meet", "gemeten",
            # FR
            "mesure", "mesurer", "valeur", "lecture", "relevé",
            # EN
            "measurement", "measure", "reading", "value", "reading",
            # Related
            "nauwkeurig", "accurate", "precisie",
        ],
    },

    {
        "canonical": "niveau",
        "category": "measurement",
        "terms": [
            # NL
            "niveau", "waterniveau", "peil", "waterpeil", "stand", "hoogte",
            "waterstand", "waterhoogte", "niveaumeting", "niveauregeling",
            "vlotter", "vlotterschakelaar", "niveauschakelaar",
            "niveauregelaar", "niveauswitch", "niveausensor",
            # FR
            "niveau", "niveau d'eau", "niveau de l'eau", "hauteur",
            "hauteur d'eau", "flotteur", "interrupteur à flotteur",
            "capteur de niveau",
            # EN
            "level", "water level", "level sensor", "water height",
            "height", "float", "float switch", "level switch",
        ],
    },

    # ── DEVICE & EQUIPMENT ────────────────────────────────────────────────────

    {
        "canonical": "wifipool",
        "category": "device",
        "terms": [
            # Product names
            "wifipool", "wifi pool", "wifi-pool",
            "wifipool apparaat", "wifipoolapparaat", "wifipool-apparaat",
            "wifipool toestel", "wifipooltoestel", "wifipool-toestel",
            "wifipool controller", "wifipool module",
            "beniferro apparaat", "beniferro toestel",
            "beniferro-apparaat", "beniferro-toestel",
            # Abbreviations
            "wp", "wfp",
        ],
    },

    {
        "canonical": "apparaat",
        "category": "device",
        "terms": [
            # NL
            "apparaat", "toestel", "controller", "module", "eenheid", "unit",
            "zwembadcomputer", "doseertoestel", "besturingssysteem",
            # FR
            "appareil", "dispositif", "controleur", "contrôleur", "module",
            "unité",
            # EN
            "device", "unit", "controller", "module", "system",
            "equipment",
            # DE
            "gerät", "geraet",
            # Related
            "box", "hub",
        ],
    },

    {
        "canonical": "pomp",
        "category": "device",
        "terms": [
            # NL
            "pomp", "pompen", "waterpomp", "circulatiepomp", "filterpomp",
            "circulatie pomp", "doseerpomp", "peristaltische pomp",
            "zwembadpomp", "filtratie pomp",
            # FR
            "pompe", "pompes", "pompe à eau", "pompe de circulation",
            "pompe circulation", "pompe filtration", "pompe doseuse",
            # EN
            "pump", "pumps", "water pump", "circulation pump", "filter pump",
            "dosing pump", "pool pump", "filtration pump",
        ],
    },

    {
        "canonical": "filter",
        "category": "device",
        "terms": [
            # NL
            "filter", "zwembadfilter", "zandfilter", "filterinstallatie",
            "filtratie", "filtratiesysteem",
            # FR
            "filtre", "filtre piscine", "filtre à sable", "filtration",
            "système de filtration",
            # EN
            "filter", "pool filter", "sand filter", "filtration",
            "filtration system",
        ],
    },

    {
        "canonical": "debiet",
        "category": "measurement",
        "terms": [
            # NL
            "debiet", "debietmeter", "stroomsnelheid", "flow", "flowmeter",
            "doorstroom", "waterflow", "waterdebiet", "doorstroming",
            "stromingssnelheid", "stromingssensor",
            # FR
            "debit", "débit", "debitmetre", "débitmètre", "flux",
            "vitesse d'écoulement", "débit d'eau",
            # EN
            "flow", "flow rate", "flowmeter", "flow meter", "flow sensor",
            "water flow", "flow speed",
        ],
    },

    {
        "canonical": "verwarming",
        "category": "device",
        "terms": [
            # NL
            "verwarming", "warmtepomp", "warmtewisselaar", "boiler",
            "verwarmingssysteem", "verhitter",
            # FR
            "chauffage", "pompe à chaleur", "échangeur de chaleur",
            "chauffe-eau",
            # EN
            "heater", "heat pump", "heat exchanger", "heating system",
        ],
    },

    # ── ACTIONS ───────────────────────────────────────────────────────────────

    {
        "canonical": "kalibreren",
        "category": "action",
        "terms": [
            # NL
            "kalibreren", "calibreren", "ijken", "afstellen", "instellen",
            "kalibratie", "calibratie", "ijking",
            "opnieuw kalibreren", "hercalibreren",
            # FR
            "calibrer", "étalonner", "étalonnage", "calibration",
            "recalibrer",
            # EN
            "calibrate", "calibration", "recalibrate", "re-calibrate",
            "set up", "adjust",
        ],
    },

    {
        "canonical": "resetten",
        "category": "action",
        "terms": [
            # NL
            "resetten", "reset", "herstarten", "herstart", "opnieuw opstarten",
            "factory reset", "fabrieksinstellingen", "terugzetten",
            "opnieuw instellen", "wissen", "initialiseren",
            "naar begininstellingen brengen", "begininstellingen",
            "herstellen", "standaardconfiguratie",
            "software reset", "hardware reset",
            "een software reset geven", "een hardware reset geven",
            # FR
            "reinitialiser", "réinitialiser", "reinitialisation",
            "réinitialisation", "redemarrer", "redémarrer",
            "remise à zero", "remise à zéro",
            "reset usine", "restaurer",
            # EN
            "reset", "factory reset", "restart", "reboot", "reinitialize",
            "restore defaults", "hard reset", "soft reset",
            # DE
            "zurücksetzen", "zuruecksetzen", "neustart", "werkseinstellungen",
        ],
    },

    {
        "canonical": "opstarten",
        "category": "action",
        "terms": [
            # NL
            "opstarten", "starten", "aanzetten", "aangaan", "aanslaan",
            "gaat aan", "gaat niet aan", "start niet", "start niet op",
            "slaat niet aan", "werkt niet", "inschakelen",
            # FR
            "demarrer", "démarrer", "allumer", "s'allume", "s'allumer",
            "mettre en marche", "mettre en route", "se met en route",
            "ne se met pas en route", "ne demarre pas", "ne démarre pas",
            "ne s'allume pas", "fonctionner",
            # EN
            "start", "start up", "starts", "turn on", "turning on",
            "switch on", "power on", "not on", "won't turn on",
            "does not start", "not starting", "won't start",
            # DE
            "starten", "einschalten", "anschalten", "anmachen",
            "startet nicht", "geht nicht an", "lässt sich nicht einschalten",
            "start nicht",
        ],
    },

    {
        "canonical": "verbinden",
        "category": "action",
        "terms": [
            # NL
            "verbinden", "verbinding", "verbindingen", "aansluiten", "koppelen",
            "verbinding maken", "configureren", "instellen", "connectie",
            "connecteren", "aankoppelen", "koppeling",
            "pairen", "pairing",
            # FR
            "connecter", "connexion", "se connecter", "relier",
            "coupler", "associer", "configurer",
            # EN
            "connect", "connection", "connecting", "pair", "pairing", "link",
            "linking", "set up connection", "configure",
        ],
    },

    {
        "canonical": "doseren",
        "category": "action",
        "terms": [
            # NL
            "doseren", "dosering", "dosis", "toevoegen", "bijvullen",
            "hoeveelheid", "doseerhoeveelheid",
            # FR
            "doser", "dosage", "dose", "ajouter", "quantité",
            # EN
            "dose", "dosage", "add", "dispense", "quantity",
        ],
    },

    {
        "canonical": "vervangen",
        "category": "action",
        "terms": [
            # NL
            "vervangen", "verwisselen", "omwisselen", "nieuw",
            "vervanging", "onderhoud",
            # FR
            "remplacer", "remplacement", "changer", "substituer",
            # EN
            "replace", "replacement", "swap", "change", "substitute",
        ],
    },

    {
        "canonical": "reinigen",
        "category": "action",
        "terms": [
            # NL
            "reinigen", "schoonmaken", "poetsen", "schoon",
            "reiniging", "onderhoud", "wassen", "spoelen",
            # FR
            "nettoyer", "nettoyage", "purger", "rincer",
            # EN
            "clean", "cleaning", "rinse", "flush", "maintain",
        ],
    },

    # ── CONNECTIVITY ──────────────────────────────────────────────────────────

    {
        "canonical": "wifi",
        "category": "connectivity",
        "terms": [
            # NL
            "wifi", "wi-fi", "wlan", "draadloos", "draadloze verbinding",
            "netwerk", "thuisnetwerk",
            # FR
            "wifi", "wi-fi", "réseau", "réseau sans fil", "connexion sans fil",
            # EN
            "wifi", "wi-fi", "wireless", "network", "wireless network",
            "wlan",
            # Related
            "router", "modem", "access point", "hotspot",
        ],
    },

    {
        "canonical": "internet",
        "category": "connectivity",
        "terms": [
            # NL
            "internet", "internetverbinding", "online", "cloud",
            # FR
            "internet", "connexion internet", "en ligne",
            # EN
            "internet", "online", "cloud", "internet connection",
        ],
    },

    {
        "canonical": "app",
        "category": "software",
        "terms": [
            # NL
            "app", "applicatie", "mobiele app", "smartphone app",
            "wifipool app",
            # FR
            "application", "appli", "app mobile",
            # EN
            "app", "application", "mobile app", "smartphone app",
        ],
    },

    {
        "canonical": "wachtwoord",
        "category": "connectivity",
        "terms": [
            # NL
            "wachtwoord", "paswoord", "inloggegevens", "toegangscode",
            # FR
            "mot de passe", "code d'accès", "identifiants",
            # EN
            "password", "passcode", "credentials", "login",
            # DE
            "passwort", "kennwort", "zugangscode",
        ],
    },

    {
        "canonical": "signaal",
        "category": "connectivity",
        "terms": [
            # NL
            "signaal", "signaalsterkte", "ontvangst", "bereik",
            # FR
            "signal", "force du signal", "portée", "réception",
            # EN
            "signal", "signal strength", "range", "reception",
            # DE
            "signal", "signalstärke", "reichweite", "empfang",
        ],
    },

    # ── PROBLEMS & STATUS ─────────────────────────────────────────────────────

    {
        "canonical": "probleem",
        "category": "problem",
        "terms": [
            # NL
            "probleem", "fout", "storing", "defect", "kapot",
            "werkt niet", "niet actief", "foutmelding", "melding",
            "alarm", "waarschuwing", "alert",
            # FR
            "problème", "erreur", "panne", "defaut", "défaut",
            "dysfonctionnement", "message d'erreur", "alarme",
            "avertissement",
            # EN
            "problem", "issue", "error", "fault", "defect",
            "not working", "malfunction", "warning", "alarm",
            "error message",
        ],
    },

    {
        "canonical": "offline",
        "category": "problem",
        "terms": [
            # NL
            "offline", "niet verbonden", "geen verbinding",
            "geen signaal", "bereik", "buiten bereik",
            # FR
            "hors ligne", "déconnecté", "pas de connexion",
            "sans signal",
            # EN
            "offline", "disconnected", "no connection", "no signal",
            "unreachable",
        ],
    },

    # ── SETTINGS & CONFIGURATION ──────────────────────────────────────────────

    {
        "canonical": "instellingen",
        "category": "config",
        "terms": [
            # NL
            "instellingen", "instelling", "configuratie", "configureren",
            "aanpassen", "wijzigen", "instellen", "parameters",
            # FR
            "paramètres", "paramètre", "configuration", "configurer",
            "réglages", "réglage",
            # EN
            "settings", "setting", "configuration", "configure",
            "parameters", "options", "preferences",
        ],
    },

    {
        "canonical": "timer",
        "category": "config",
        "terms": [
            # NL
            "timer", "tijdschakelaar", "automatisatie", "planning",
            "schema", "tijdplan", "programmering", "snippet",
            "automatisch", "schedule",
            # FR
            "minuterie", "programmation", "planification", "automatisation",
            "horaire",
            # EN
            "timer", "schedule", "automation", "programming",
            "timed", "scheduled",
        ],
    },

    # ── ENVIRONMENT ───────────────────────────────────────────────────────────

    {
        "canonical": "temperatuur",
        "category": "measurement",
        "terms": [
            # NL
            "temperatuur", "temp", "warmte", "kou", "koude",
            "watertemperatuur", "luchttemperatuur",
            # FR
            "température", "temp", "chaleur", "froid",
            "température de l'eau",
            # EN
            "temperature", "temp", "heat", "cold", "water temperature",
        ],
    },

    {
        "canonical": "condensatie",
        "category": "environment",
        "terms": [
            # NL
            "condensatie", "condenswater", "vocht", "vochtigheid",
            "dauw", "druppels", "waterdruppels", "zweet",
            # FR
            "condensation", "humidité", "buée", "vapeur d'eau",
            # EN
            "condensation", "moisture", "humidity", "dew", "water droplets",
        ],
    },

    # ── POOL TYPES ────────────────────────────────────────────────────────────

    {
        "canonical": "zwembad",
        "category": "pool_type",
        "terms": [
            # NL
            "zwembad", "bad", "bassein", "buitenzwembad", "binnenzwembad",
            "privézwembad", "familiezwembad",
            # FR
            "piscine", "bassin", "piscine extérieure", "piscine intérieure",
            # EN
            "pool", "swimming pool", "outdoor pool", "indoor pool",
        ],
    },

    {
        "canonical": "spa",
        "category": "pool_type",
        "terms": [
            # NL
            "spa", "jacuzzi", "whirlpool", "bubbelbad", "bad",
            "hottub", "hot tub",
            # FR
            "spa", "jacuzzi", "bain à remous",
            # EN
            "spa", "jacuzzi", "whirlpool", "hot tub",
        ],
    },

    # ── HANDLEIDINGEN & SUPPORT ───────────────────────────────────────────────

    {
        "canonical": "handleiding",
        "category": "support",
        "terms": [
            # NL
            "handleiding", "gebruiksaanwijzing", "installatiegids",
            "instructies", "documentatie", "gids",
            # FR
            "manuel", "guide", "instructions", "documentation",
            "mode d'emploi",
            # EN
            "manual", "guide", "instructions", "documentation",
            "user guide",
        ],
    },

    # ── GENERATION VARIANTS ───────────────────────────────────────────────────

    {
        "canonical": "gen1",
        "category": "device",
        "terms": [
            "gen1", "gen 1", "generation 1", "generatie 1", "g1",
            "eerste generatie", "1ste generatie",
        ],
    },

    {
        "canonical": "gen2",
        "category": "device",
        "terms": [
            "gen2", "gen 2", "generation 2", "generatie 2", "g2",
            "tweede generatie", "2de generatie",
        ],
    },

    {
        "canonical": "gen3",
        "category": "device",
        "terms": [
            "gen3", "gen 3", "generation 3", "generatie 3", "g3",
            "derde generatie", "3de generatie",
        ],
    },

    # ── ETHERNET & NETWORK ────────────────────────────────────────────────────

    {
        "canonical": "ethernet",
        "category": "connectivity",
        "terms": [
            "ethernet", "internetkabel", "netwerkkabel", "lan",
            "bedrade verbinding", "rj45",
            "câble réseau", "câble ethernet",
            "network cable", "wired connection",
        ],
    },

    # ── COMMON PHRASES & STATES ───────────────────────────────────────────────

    {
        "canonical": "te laag",
        "category": "state",
        "terms": [
            # NL
            "te laag", "laag", "te weinig", "onvoldoende",
            # FR
            "trop bas", "trop basse", "bas", "basse", "insuffisant",
            # EN
            "too low", "low", "insufficient", "not enough",
        ],
    },

    {
        "canonical": "te hoog",
        "category": "state",
        "terms": [
            # NL
            "te hoog", "hoog", "te veel", "overmatig",
            # FR
            "trop haut", "trop haute", "haut", "haute", "excessif",
            # EN
            "too high", "high", "excessive", "too much",
        ],
    },

    {
        "canonical": "instellen",
        "category": "action",
        "terms": [
            # NL
            "instellen", "setup", "configureren", "aanpassen",
            "opzetten", "regelen",
            # FR
            "configurer", "paramétrer", "régler", "ajuster",
            # EN
            "setup", "set up", "configure", "adjust", "tune",
        ],
    },

    {
        "canonical": "werkt niet",
        "category": "problem",
        "terms": [
            # NL
            "werkt niet", "doet het niet", "niet actief", "kapot",
            "defect", "stuk",
            # FR
            "ne fonctionne pas", "ne marche pas", "en panne",
            "défectueux", "cassé",
            # EN
            "not working", "doesn't work", "broken", "defective",
            "not functioning", "malfunction",
        ],
    },
]


# ============================================================================
# BUILD LOOKUP TABLES AT STARTUP
# ============================================================================

# term -> canonical
_TERM_TO_CANONICAL: Dict[str, str] = {}

# canonical -> all terms (for expansion)
_CANONICAL_TO_TERMS: Dict[str, List[str]] = {}

# category -> set of canonicals
_CATEGORY_CANONICALS: Dict[str, Set[str]] = {}


def _load_excel_synonym_groups() -> List[Dict]:
    """Load extra synonym groups written by excel_loader.py."""
    import json
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "data", "excel_synonyms.json",
    )
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("Could not load Excel synonyms: %s", e)
        return []

    extra: List[Dict] = []
    for i, group in enumerate(raw):
        if not isinstance(group, list) or len(group) < 2:
            continue
        terms = [str(t).strip().lower() for t in group if t and str(t).strip()]
        if len(terms) < 2:
            continue
        extra.append({
            "canonical": terms[0],
            "category": "excel_import",
            "terms": terms,
        })
    return extra


def _build_lookup_maps() -> None:
    """Build lookup maps from SYNONYM_GROUPS + Excel groups at module load time."""
    all_groups = SYNONYM_GROUPS + _load_excel_synonym_groups()
    for group in all_groups:
        canonical = group["canonical"].lower().strip()
        category = group.get("category", "general")
        terms = [t.lower().strip() for t in group["terms"]]

        # Ensure canonical is in its own list
        if canonical not in terms:
            terms.insert(0, canonical)

        # Map each term → canonical (only if not already mapped to something else)
        for term in terms:
            clean_term = _strip_accents(term)
            if term not in _TERM_TO_CANONICAL:
                _TERM_TO_CANONICAL[term] = canonical
            if clean_term not in _TERM_TO_CANONICAL:
                _TERM_TO_CANONICAL[clean_term] = canonical

        # Map canonical → all terms (merge if canonical already seen)
        if canonical in _CANONICAL_TO_TERMS:
            existing = _CANONICAL_TO_TERMS[canonical]
            for t in terms:
                if t not in existing:
                    existing.append(t)
        else:
            _CANONICAL_TO_TERMS[canonical] = terms

        # Category tracking
        if category not in _CATEGORY_CANONICALS:
            _CATEGORY_CANONICALS[category] = set()
        _CATEGORY_CANONICALS[category].add(canonical)


def _strip_accents(text: str) -> str:
    """Strip accents from text for accent-insensitive matching."""
    import unicodedata
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if not unicodedata.combining(c))


# Build at import time
_build_lookup_maps()
logger.debug(f"Synonym lookup built: {len(_TERM_TO_CANONICAL)} terms → {len(_CANONICAL_TO_TERMS)} canonicals")


# ============================================================================
# PUBLIC API
# ============================================================================

@lru_cache(maxsize=1024)
def normalize_with_synonyms(text: str) -> str:
    """
    Replace synonym variants with their canonical forms.

    This ensures "zuurtegraad" and "acidité" and "acidity" all
    become "ph" before embedding search, so they find the same results.

    Args:
        text: Input text (query or document)

    Returns:
        Text with synonyms replaced by canonical forms
    """
    if not text:
        return text

    # Tokenize by whitespace + punctuation boundaries
    # We try multi-word phrases first (longest match), then single words
    result = _replace_phrases(text.lower())
    return result


@lru_cache(maxsize=512)
def expand_with_synonyms(text: str, max_extra_terms: int = 8) -> str:
    """
    Add synonym expansions to a query for broader coverage.

    Instead of REPLACING terms, this ADDS their synonyms to the text.
    Useful for enriching search queries so the embedding finds more matches.

    Example:
        "pH too low" → "pH too low zuurtegraad acidite acid level"

    Args:
        text: Input text (usually a search query)
        max_extra_terms: Max synonym terms to add

    Returns:
        Original text + synonyms appended
    """
    if not text:
        return text

    text_lower = text.lower()
    extra_terms: List[str] = []
    found_canonicals: Set[str] = set()

    # Find which canonical terms appear in the text
    for term, canonical in _TERM_TO_CANONICAL.items():
        if canonical in found_canonicals:
            continue

        # Check if the term appears in text (word boundary aware)
        pattern = r'\b' + re.escape(term) + r'\b'
        if re.search(pattern, text_lower):
            found_canonicals.add(canonical)

            # Add synonyms from this canonical's group
            synonyms = _CANONICAL_TO_TERMS.get(canonical, [])
            for syn in synonyms:
                # Don't re-add things already in text
                if syn not in text_lower and syn not in extra_terms:
                    extra_terms.append(syn)
                    if len(extra_terms) >= max_extra_terms:
                        break

        if len(extra_terms) >= max_extra_terms:
            break

    if extra_terms:
        return text + " " + " ".join(extra_terms)

    return text


def get_synonyms(word: str) -> List[str]:
    """
    Get all synonyms for a given word.

    Args:
        word: Input word (in any language)

    Returns:
        List of all synonyms including the word itself
    """
    word_lower = word.lower().strip()

    # Try direct lookup
    canonical = _TERM_TO_CANONICAL.get(word_lower)
    if canonical is None:
        # Try without accents
        canonical = _TERM_TO_CANONICAL.get(_strip_accents(word_lower))

    if canonical is None:
        return [word]

    return _CANONICAL_TO_TERMS.get(canonical, [word])


def get_canonical(word: str) -> str:
    """
    Get the canonical form of a word.

    Args:
        word: Input word (in any language)

    Returns:
        Canonical form, or original word if not found
    """
    word_lower = word.lower().strip()
    return _TERM_TO_CANONICAL.get(word_lower) or _TERM_TO_CANONICAL.get(_strip_accents(word_lower)) or word


def get_related_terms(text: str) -> Set[str]:
    """
    Get all synonym groups touched by the given text.

    Useful for domain detection: if text mentions "zuurtegraad",
    returns all pH-related synonyms.

    Args:
        text: Input text to analyze

    Returns:
        Set of all related terms from matched synonym groups
    """
    text_lower = text.lower()
    related: Set[str] = set()
    found_canonicals: Set[str] = set()

    for term, canonical in _TERM_TO_CANONICAL.items():
        if canonical in found_canonicals:
            continue
        if term in text_lower:
            found_canonicals.add(canonical)
            related.update(_CANONICAL_TO_TERMS.get(canonical, []))

    return related


def detect_domains_in_text(text: str) -> Set[str]:
    """
    Detect which domain categories are mentioned in text.

    Args:
        text: Input text

    Returns:
        Set of domain categories (e.g., {"chemistry", "sensor"})
    """
    text_lower = text.lower()
    found_domains: Set[str] = set()

    for group in SYNONYM_GROUPS:
        canonical = group["canonical"].lower()
        category = group.get("category", "general")

        if category in found_domains:
            continue

        # Check if any term from this group appears in the text
        for term in group["terms"]:
            if term.lower() in text_lower:
                found_domains.add(category)
                break

    return found_domains


def _replace_phrases(text: str) -> str:
    """
    Replace synonym phrases in text with canonical forms.
    Tries longest phrases first to avoid partial replacement errors.

    Args:
        text: Normalized (lowercase) input text

    Returns:
        Text with synonyms replaced
    """
    # Sort terms by length (longest first) to handle multi-word matches first
    sorted_terms = sorted(_TERM_TO_CANONICAL.keys(), key=len, reverse=True)

    result = text
    replaced: Set[str] = set()

    for term in sorted_terms:
        if len(term) < 3:  # Skip very short terms to avoid false matches
            continue
        if term in replaced:
            continue

        canonical = _TERM_TO_CANONICAL[term]

        # Skip if term == canonical (nothing to replace)
        if term == canonical:
            continue

        # Word-boundary aware replacement
        pattern = r'\b' + re.escape(term) + r'\b'
        new_result = re.sub(pattern, canonical, result)

        if new_result != result:
            result = new_result
            replaced.add(term)

    return result


def fuzzy_get_canonical(word: str, threshold: int = 82) -> Optional[str]:
    """
    Fuzzy lookup: find the canonical form of a word even with typos.

    Uses RapidFuzz (already in requirements.txt) for fast similarity matching.
    Only activates when exact match fails.

    Args:
        word: Input word (potentially misspelled)
        threshold: Minimum similarity score (0-100). 82 = allows ~2 typos.

    Returns:
        Canonical form if match found above threshold, None otherwise

    Examples:
        "zuurtegraad" → "ph"
        "caliibrate"  → "kalibreren"  (typo)
        "kalibreer"   → "kalibreren"  (Dutch conjugation)
        "reinitialise"→ "resetten"
    """
    word_lower = word.lower().strip()

    # Fast path: exact match first
    exact = _TERM_TO_CANONICAL.get(word_lower) or _TERM_TO_CANONICAL.get(_strip_accents(word_lower))
    if exact:
        return exact

    # Skip short words (too many false positives)
    if len(word_lower) < 4:
        return None

    try:
        from rapidfuzz import process, fuzz

        # Only search against terms of similar length (±40%) for efficiency
        min_len = int(len(word_lower) * 0.6)
        max_len = int(len(word_lower) * 1.4)
        candidates = [t for t in _TERM_TO_CANONICAL if min_len <= len(t) <= max_len]

        if not candidates:
            return None

        match, score, _ = process.extractOne(
            word_lower,
            candidates,
            scorer=fuzz.token_sort_ratio,
        )

        if score >= threshold:
            return _TERM_TO_CANONICAL.get(match)

    except ImportError:
        pass  # rapidfuzz not installed

    return None


@lru_cache(maxsize=512)
def expand_with_synonyms_fuzzy(text: str, max_extra_terms: int = 6) -> str:
    """
    Like expand_with_synonyms but also handles typos via fuzzy matching.

    This is the most powerful version:
    1. Exact synonym expansion (fast, precise)
    2. Fuzzy synonym expansion (catches typos, conjugations, abbreviations)

    Args:
        text: Input query text
        max_extra_terms: Max synonyms to append

    Returns:
        Original text + synonyms appended
    """
    # First do exact expansion
    result = expand_with_synonyms(text, max_extra_terms=max_extra_terms)

    # Then try fuzzy for tokens that didn't match exactly
    text_lower = text.lower()
    tokens = re.findall(r'\b[a-z]{4,}\b', text_lower)  # only words 4+ chars

    extra: List[str] = []
    found_canonicals: Set[str] = set()

    for tok in tokens:
        # Skip if already matched by exact expansion
        if tok in _TERM_TO_CANONICAL or _strip_accents(tok) in _TERM_TO_CANONICAL:
            continue

        canonical = fuzzy_get_canonical(tok, threshold=82)
        if canonical and canonical not in found_canonicals:
            found_canonicals.add(canonical)
            synonyms = _CANONICAL_TO_TERMS.get(canonical, [])
            for syn in synonyms[:3]:
                if syn not in result and syn not in extra:
                    extra.append(syn)

    if extra:
        result = result + " " + " ".join(extra[:max_extra_terms])

    return result


def stats() -> Dict:
    """Return statistics about the synonym dictionary."""
    total_terms = sum(len(v) for v in _CANONICAL_TO_TERMS.values())
    return {
        "total_groups": len(SYNONYM_GROUPS),
        "total_canonicals": len(_CANONICAL_TO_TERMS),
        "total_terms": total_terms,
        "total_lookup_entries": len(_TERM_TO_CANONICAL),
        "categories": {cat: len(cans) for cat, cans in _CATEGORY_CANONICALS.items()},
    }
