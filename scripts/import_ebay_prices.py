"""
Collecte d'annonces eBay pour les cartes déjà en base — CartaBourse

Prérequis :
  1. Un compte développeur eBay (voir configuration-ebay-api.md)
  2. listings-external-ref.sql ET ajout-langue-listings.sql exécutés dans Supabase
  3. pip install requests

Utilisation :
  EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... EBAY_ENV=sandbox EBAY_MARKETPLACE=EBAY_FR \
  SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_KEY=... \
  python import_ebay_listings.py

Ce script :
  - traite un LOT LIMITÉ de cartes à chaque exécution (CARD_BATCH_SIZE),
    pas tout le catalogue d'un coup — pour rester sous la limite d'appels
    quotidienne de l'API eBay (voir configuration-ebay-api.md). Chaque carte
    cherchée = 1 appel, quel que soit le nombre de résultats qu'elle ramène.
  - pour chaque carte, cherche sur eBay par nom de carte + nom d'extension
    + numéro, dans la langue correspondant à la marketplace ciblée
  - ne garde que les annonces dont le TITRE contient vraiment le nom ET le
    numéro de la carte cherchée (tolérant aux variations d'écriture du
    numéro : zéros de tête, espaces, '#'...) — élimine les lots multi-cartes
    et résultats hors sujet
  - met à jour les annonces déjà connues (prix changé...) et en ajoute
    de nouvelles, grâce à l'identifiant d'annonce eBay (external_ref) —
    pas de doublon créé à chaque exécution
  - repère une éventuelle gradation ET la langue de l'exemplaire via le
    titre de l'annonce : mots-clés de langue, ET nom d'extension traduit
    (ex. "Set de Base" ne s'écrit qu'en français) comme indice plus fiable
    (heuristique basique, limitée aux informations disponibles au niveau
    résultat de recherche — récupérer les caractéristiques détaillées
    coûterait un appel par annonce au lieu d'un par carte, ce qui
    exploserait le quota quotidien)

NON couvert pour l'instant, à réévaluer plus tard si besoin :
  - détection de la langue par lecture de l'image de l'annonce (OCR/vision) —
    demande un service d'analyse d'image dédié (payant ou à héberger), un
    chantier à part entière, pas ajouté ici pour rester sur une base simple
    et gratuite
  - enchères (AUCTION) : volontairement exclues pour l'instant (voir
    search_ebay) — prévu de revenir dessus avec une colonne dédiée au type
    de vente sur listings
"""

import os
import re
import sys
import time
import base64
import requests

EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET")
EBAY_ENV = os.environ.get("EBAY_ENV", "sandbox")  # "sandbox" ou "production"
EBAY_MARKETPLACE = os.environ.get("EBAY_MARKETPLACE", "EBAY_US")  # pas de "global" chez eBay : EBAY_US est le catalogue le plus fourni

# Langue à utiliser pour construire la requête de recherche, selon la
# marketplace ciblée — un nom de carte en français cherche mieux sur EBAY_FR
# qu'un nom anglais, et inversement.
MARKETPLACE_LOCALE = {"EBAY_FR": "fr", "EBAY_DE": "de"}
SEARCH_LOCALE = MARKETPLACE_LOCALE.get(EBAY_MARKETPLACE, "en")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

CARD_BATCH_SIZE = int(os.environ.get("CARD_BATCH_SIZE", "4500"))  # 1 carte cherchée = 1 appel eBay ; marge de sécurité sous la limite de 5000/jour
RESULTS_PER_CARD = 15  # annonces récupérées par carte (n'affecte PAS le nombre d'appels : toujours 1 par carte)

if not all([EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, SUPABASE_URL, SUPABASE_SERVICE_KEY]):
    sys.exit("Erreur : EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, SUPABASE_URL et SUPABASE_SERVICE_KEY "
              "doivent être définies en variables d'environnement.")

EBAY_BASE = "https://api.ebay.com" if EBAY_ENV == "production" else "https://api.sandbox.ebay.com"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=representation",
}


def get_with_retry(method, url, max_attempts=5, **kwargs):
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
        except requests.exceptions.RequestException as e:
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt
            print(f"    Erreur réseau ({e}) — nouvel essai dans {wait}s ({attempt}/{max_attempts})...")
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504, 429):
            if attempt == max_attempts:
                resp.raise_for_status()
            wait = 2 ** attempt
            print(f"    Erreur temporaire ({resp.status_code}) — nouvel essai dans {wait}s ({attempt}/{max_attempts})...")
            time.sleep(wait)
            continue
        return resp
    return None


# ---------------------------------------------------------------
# Authentification eBay (jeton d'application, valable ~2h)
# ---------------------------------------------------------------
_ebay_token = {"value": None, "expires_at": 0}

def get_ebay_token():
    if _ebay_token["value"] and time.time() < _ebay_token["expires_at"] - 60:
        return _ebay_token["value"]

    credentials = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    resp = get_with_retry(
        "POST", f"{EBAY_BASE}/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
    )
    resp.raise_for_status()
    data = resp.json()
    _ebay_token["value"] = data["access_token"]
    _ebay_token["expires_at"] = time.time() + data.get("expires_in", 7200)
    return _ebay_token["value"]


def search_ebay(query, limit=RESULTS_PER_CARD):
    token = get_ebay_token()
    resp = get_with_retry(
        "GET", f"{EBAY_BASE}/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE,
        },
        # Enchères volontairement exclues pour l'instant (choix du 03/08) : le
        # prix d'une enchère en cours n'est pas comparable à un prix affiché
        # (champ différent, currentBidPrice, et pas encore le prix final réel).
        # À reconsidérer avec une colonne dédiée au type de vente sur listings.
        params={"q": query, "limit": limit, "filter": "buyingOptions:{FIXED_PRICE}"},
    )
    if resp.status_code != 200:
        print(f"    Erreur recherche eBay ({resp.status_code}) pour \"{query}\" : {resp.text[:200]}")
        return []
    return resp.json().get("itemSummaries", [])


# ---------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------
def supabase_get(path):
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=SUPABASE_HEADERS)
    resp.raise_for_status()
    return resp.json()


def supabase_upsert(table, rows, on_conflict):
    if not rows:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    resp = requests.post(url, headers=SUPABASE_HEADERS, json=rows)
    if resp.status_code not in (200, 201):
        print(f"  Erreur upsert {table} : {resp.status_code} — {resp.text[:300]}")
        return []
    return resp.json()


def supabase_patch(path, body):
    resp = requests.patch(f"{SUPABASE_URL}/rest/v1/{path}", headers=SUPABASE_HEADERS, json=body)
    if resp.status_code not in (200, 204):
        print(f"  Erreur mise à jour : {resp.status_code} — {resp.text[:300]}")


# ---------------------------------------------------------------
# Détection basique de gradation dans le titre de l'annonce
# ---------------------------------------------------------------
def build_grading_pattern(grading_companies):
    names = "|".join(re.escape(c["name"]) for c in grading_companies)
    return re.compile(rf"\b({names})\s*(\d{{1,2}}(?:\.\d)?)\b", re.IGNORECASE)


def detect_grading(title, pattern, grading_companies_by_name):
    match = pattern.search(title)
    if not match:
        return None, None
    company_name, grade = match.group(1), match.group(2)
    company = grading_companies_by_name.get(company_name.upper())
    return (company["id"], float(grade)) if company else (None, None)


# ---------------------------------------------------------------
# Détection basique de la langue de l'exemplaire, sur le même principe
# ---------------------------------------------------------------
LANGUAGE_KEYWORDS = {
    "fr": r"\b(fr|fra|fran[cç]ais|francais|french)\b",
    "de": r"\b(de|deu|deutsch|german)\b",
    "en": r"\b(en|eng|english|anglais)\b",
    "ja": r"\b(jp|jpn|japanese|japonais|japan)\b",
    "es": r"\b(es|esp|spanish|espa[nñ]ol)\b",
    "it": r"\b(it|ita|italian|italiano)\b",
}
LANGUAGE_PATTERNS = {locale: re.compile(pattern, re.IGNORECASE) for locale, pattern in LANGUAGE_KEYWORDS.items()}


def detect_language(title, set_names_by_locale=None):
    # 1. Indice fort : le nom de l'extension, tel qu'il varie d'une langue à
    # l'autre, mentionné dans le titre (ex. "Set de Base" ne s'écrit qu'en
    # français) — plus fiable qu'un simple mot-clé générique.
    if set_names_by_locale:
        title_lower = title.lower()
        for locale, name in set_names_by_locale.items():
            if name and locale in LANGUAGE_KEYWORDS and name.lower() in title_lower:
                return locale

    # 2. Repli : mots-clés génériques de langue
    for locale, pattern in LANGUAGE_PATTERNS.items():
        if pattern.search(title):
            return locale

    return None  # rien de fiable trouvé — mieux vaut NULL qu'une supposition


def build_number_pattern(card_number, total_cards):
    """Le numéro d'une carte s'écrit de façons variées selon le vendeur
    (avec ou sans zéros de tête, espaces autour du '/', '#'...). On construit
    un motif tolérant plutôt qu'une chaîne figée."""
    try:
        bare_number = str(int(card_number))  # retire les zéros de tête si numérique
    except ValueError:
        bare_number = re.escape(card_number)  # numéro non numérique (promos, ex: 'SWSH001') : recherche littérale
    if total_cards:
        return re.compile(rf"\b0*{bare_number}\s*/\s*0*{total_cards}\b")
    return re.compile(rf"(?:\b0*{bare_number}\s*/|#\s*0*{bare_number}\b)")


def main():
    # Nécessaire sur GitHub Actions (et plus généralement dès que le script
    # n'est pas lancé depuis son propre dossier) : sans ça, le fichier de
    # progression serait cherché/écrit au mauvais endroit.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    site_id = supabase_get("sites?name=eq.eBay&select=id")[0]["id"]
    grading_companies = supabase_get("grading_companies?select=id,name")
    grading_companies_by_name = {c["name"].upper(): c for c in grading_companies}
    grading_pattern = build_grading_pattern(grading_companies)

    # Cartes à traiter : on avance dans le catalogue à chaque exécution grâce
    # à un petit fichier de progression local (id de la dernière carte traitée),
    # plutôt que de toujours retraiter les mêmes premières cartes.
    progress_file = "ebay_progress.txt"
    last_id = 0
    if os.path.exists(progress_file):
        with open(progress_file, encoding="utf-8") as f:
            last_id = int(f.read().strip() or 0)

    cards = supabase_get(
        f"cards?select=id,card_number,sets(name_en,total_cards,set_names(locale,name)),card_names(locale,name)"
        f"&id=gt.{last_id}&order=id.asc&limit={CARD_BATCH_SIZE}"
    )

    if not cards:
        print("Fin du catalogue atteinte — on repart du début au prochain lancement.")
        with open(progress_file, "w", encoding="utf-8") as f:
            f.write("0")
        return

    print(f"{len(cards)} carte(s) à traiter cette fois-ci (marketplace : {EBAY_MARKETPLACE}), à partir de l'id {last_id}.")

    for i, card in enumerate(cards, 1):
        names = card["card_names"]
        card_name = next((n["name"] for n in names if n["locale"] == SEARCH_LOCALE), None) \
            or next((n["name"] for n in names if n["locale"] == "en"), None)
        if not card_name:
            continue

        set_info = card["sets"] or {}
        set_names = set_info.get("set_names") or []
        set_names_by_locale = {n["locale"]: n["name"] for n in set_names}
        set_names_by_locale.setdefault("en", set_info.get("name_en", ""))
        set_name = set_names_by_locale.get(SEARCH_LOCALE) or set_info.get("name_en", "")

        number_part = f"{card['card_number']}/{set_info['total_cards']}" if set_info.get("total_cards") else card["card_number"]
        query = f"{card_name} {set_name} {number_part}".strip()

        print(f"[{i}/{len(cards)}] {query}...")
        items = search_ebay(query)

        # Ne garde que les annonces dont le titre contient vraiment le nom
        # ET le numéro de la carte cherchée — élimine les lots multi-cartes,
        # les autres cartes du même Pokémon dans d'autres extensions, etc.
        card_name_lower = card_name.lower()
        number_pattern = build_number_pattern(card["card_number"], set_info.get("total_cards"))
        items = [
            it for it in items
            if card_name_lower in it.get("title", "").lower() and number_pattern.search(it.get("title", ""))
        ]

        rows = []
        for item in items:
            title = item.get("title", "")
            grading_company_id, grade = detect_grading(title, grading_pattern, grading_companies_by_name)
            price = item.get("price", {})
            rows.append({
                "card_id": card["id"],
                "site_id": site_id,
                "seller_name": item.get("seller", {}).get("username"),
                "price": price.get("value"),
                "currency": price.get("currency", "EUR"),
                "is_graded": grading_company_id is not None,
                "grading_company_id": grading_company_id,
                "grade": grade,
                "condition": None,  # eBay ne fournit pas de correspondance fiable avec NM/EX/GD/PL
                "language": detect_language(title, set_names_by_locale),
                "listing_url": item.get("itemWebUrl"),
                "external_ref": item.get("itemId"),
                "is_active": True,
            })

        supabase_upsert("listings", rows, on_conflict="dedupe_key")
        print(f"  {len(rows)} annonce(s) trouvée(s).")
        time.sleep(0.3)

    with open(progress_file, "w", encoding="utf-8") as f:
        f.write(str(cards[-1]["id"]))

    print("\nCollecte terminée pour ce lot.")
    print(f"Relancez le script pour traiter la suite du catalogue à partir de l'id {cards[-1]['id']} "
          "(pensez à automatiser ça avec une tâche planifiée une fois validé manuellement).")


if __name__ == "__main__":
    main()
