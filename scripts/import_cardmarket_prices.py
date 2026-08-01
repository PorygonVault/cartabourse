"""
Collecte des prix Cardmarket via TCGdex.dev — CartaBourse

Contrairement à eBay, aucune inscription ni clé API n'est nécessaire :
TCGdex inclut directement les statistiques de prix Cardmarket (moyennes,
tendance, prix bas) dans la fiche de chaque carte.

Important : ce sont des statistiques de MARCHÉ AGRÉGÉES (comme celles que
Cardmarket affiche lui-même sur ses propres pages), pas des annonces
vendeur individuelles. Ça alimente price_history (le futur graphique de
prix), pas listings (le tableau "exemplaires référencés", qui restera lié
à eBay et à une éventuelle collecte Cardmarket au niveau vendeur plus tard).

Prérequis :
  1. price-history-fix.sql exécuté dans Supabase
  2. pip install requests

Utilisation :
  SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_KEY=... \
  python import_cardmarket_prices.py
"""

import os
import sys
import time
import datetime
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
# Supabase (PostgREST) plafonne chaque requête à 1000 lignes par défaut,
# quelle que soit la valeur demandée dans "limit" — inutile de monter plus
# haut, ça serait silencieusement ramené à 1000 de toute façon. Le script
# boucle en interne sur des pages de cette taille pour couvrir tout le
# catalogue en une seule exécution (voir main()).
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "1000"))

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    sys.exit("Erreur : SUPABASE_URL et SUPABASE_SERVICE_KEY doivent être définies en variables d'environnement.")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=representation",
}
TCGDEX_BASE = "https://api.tcgdex.net/v2"
PROGRESS_FILE = "cardmarket_progress.txt"


def get_with_retry(url, allow_404=True, max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, timeout=30)
        except requests.exceptions.RequestException as e:
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt
            print(f"    Erreur réseau ({e}) — nouvel essai dans {wait}s ({attempt}/{max_attempts})...")
            time.sleep(wait)
            continue
        if allow_404 and resp.status_code == 404:
            return None
        if resp.status_code in (500, 502, 503, 504, 429):
            if attempt == max_attempts:
                resp.raise_for_status()
            wait = 2 ** attempt
            print(f"    Erreur temporaire ({resp.status_code}) — nouvel essai dans {wait}s ({attempt}/{max_attempts})...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()


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


def process_page(cards, today):
    """Traite une page de cartes : interroge TCGdex et upsert les prix trouvés.
    Renvoie (nb_avec_prix, nb_sans_prix)."""
    rows = []
    no_pricing = 0

    for i, card in enumerate(cards, 1):
        set_code = card["sets"]["code"] if card["sets"] else None
        if not set_code:
            continue
        tcgdex_id = f"{set_code}-{card['card_number']}"

        detail = get_with_retry(f"{TCGDEX_BASE}/en/cards/{tcgdex_id}")
        cardmarket = (detail or {}).get("pricing", {}).get("cardmarket")

        if not cardmarket:
            no_pricing += 1
            continue

        rows.append({
            "card_id": card["id"],
            "period_date": today,
            "avg_price": cardmarket.get("avg"),
            "min_price": cardmarket.get("low"),
            "max_price": None,  # Cardmarket ne fournit pas de "haut" agrégé, seulement bas/moyenne/tendance
            "currency": cardmarket.get("unit", "EUR"),
        })

        if i % 100 == 0:
            print(f"    [{i}/{len(cards)}] traité...")
        time.sleep(0.1)

    supabase_upsert("price_history", rows, on_conflict="target_key,period_date,currency")
    return len(rows), no_pricing


def main():
    # Nécessaire sur GitHub Actions (et plus généralement dès que le script
    # n'est pas lancé depuis son propre dossier) : sans ça, le fichier de
    # progression seraient cherché/écrit au mauvais endroit.
    last_id = 0      os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            last_id = int(f.read().strip() or 0)

    today = datetime.date.today().isoformat()
    total_with_price = 0
    total_without_price = 0
    total_cards = 0
    page_num = 0

    print(f"Départ à l'id {last_id}, pages de {PAGE_SIZE}.")

    while True:
        page_num += 1
        cards = supabase_get(
            f"cards?select=id,card_number,sets(code)&id=gt.{last_id}&order=id.asc&limit={PAGE_SIZE}"
        )

        if not cards:
            print("Fin du catalogue atteinte — on repart du début au prochain lancement.")
            last_id = 0
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                f.write("0")
            break

        print(f"Page {page_num} : {len(cards)} carte(s), à partir de l'id {last_id}...")
        with_price, without_price = process_page(cards, today)
        total_with_price += with_price
        total_without_price += without_price
        total_cards += len(cards)

        last_id = cards[-1]["id"]
        # Écrit la progression après CHAQUE page (pas seulement à la fin) :
        # une interruption en cours de route ne fait pas tout reperdre.
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            f.write(str(last_id))

        if len(cards) < PAGE_SIZE:
            print("Fin du catalogue atteinte dans cette page — on repart du début au prochain lancement.")
            last_id = 0
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                f.write("0")
            break

    print(f"\n{total_cards} carte(s) traitée(s) au total cette exécution.")
    print(f"{total_with_price} avec un prix Cardmarket enregistré, {total_without_price} sans prix disponible.")
    print("Pour un vrai historique, ce script doit être relancé chaque jour (ex. via une tâche planifiée) "
          "afin d'ajouter un nouveau point à price_history quotidiennement.")


if __name__ == "__main__":
    main()