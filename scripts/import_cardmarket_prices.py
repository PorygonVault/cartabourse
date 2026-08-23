"""
Collecte des prix Cardmarket via le fichier price guide officiel
CartaBourse

Remplace l'ancienne version (qui interrogeait TCGdex carte par carte,
avec le prix avg1 — ne se mettait à jour qu'une fois par semaine).
Utilise maintenant directement le fichier officiel de Cardmarket, avec
le prix "trend" (mis à jour chaque jour, confirmé sur ce projet), stocké
dans une colonne "price" volontairement neutre — si la méthode de calcul
change encore un jour, pas besoin de renommer la colonne à nouveau.

Fait deux choses, dans l'ordre, à chaque exécution :
  1. Télécharge le fichier price_guide_6.json depuis Cardmarket, et le
     commit/push dans le dépôt Git (utile pour l'historique, le debug,
     et pour que d'autres outils du dépôt puissent s'en servir sans
     retélécharger) — mêmes commandes git que vos automatisations
     GitHub Actions existantes (ebay-daily.yml, etc.)
  2. Utilise ce même fichier pour mettre à jour price_history, en
     rapprochant via card_variants.cardmarket_id (jamais par nom).

Purge automatique : comme avant, les points de plus de 2 semaines sont
supprimés à chaque exécution.

Prérequis :
  1. reinit-price-history-colonne-price.sql exécuté dans Supabase
  2. pip install requests
  3. Lancé depuis un dépôt Git déjà cloné (avec les identifiants git
     configurés — c'est le cas automatiquement dans GitHub Actions)

Utilisation :
  SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_KEY=... \
  python import_cardmarket_prices.py
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys
import json
import time
import datetime
import subprocess
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "14"))
PRICE_GUIDE_URL = os.environ.get(
    "PRICE_GUIDE_URL", "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json"
)
LOCAL_JSON_PATH = os.environ.get("LOCAL_JSON_PATH", "price_guide_6.json")
CARD_CATEGORY_ID = 51  # cartes individuelles chez Cardmarket — 52/53 = produits scellés
UPSERT_BATCH_SIZE = int(os.environ.get("UPSERT_BATCH_SIZE", "500"))
SKIP_GIT_PUSH = os.environ.get("SKIP_GIT_PUSH", "false").lower() == "true"  # pratique pour tester en local

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    sys.exit("Erreur : SUPABASE_URL et SUPABASE_SERVICE_KEY doivent être définies en variables d'environnement.")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=minimal",
}


def get_with_retry(url, max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, timeout=120)
        except requests.exceptions.RequestException as e:
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt
            print(f"  Erreur réseau ({e}) — nouvel essai dans {wait}s ({attempt}/{max_attempts})...")
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504, 429):
            if attempt == max_attempts:
                resp.raise_for_status()
            wait = 2 ** attempt
            print(f"  Erreur temporaire ({resp.status_code}) — nouvel essai dans {wait}s ({attempt}/{max_attempts})...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    return None


def supabase_get_all(path):
    all_rows = []
    offset = 0
    while True:
        sep = "&" if "?" in path else "?"
        resp = requests.get(f"{SUPABASE_URL}/rest/v1/{path}{sep}limit=1000&offset={offset}", headers=SUPABASE_HEADERS)
        resp.raise_for_status()
        page = resp.json()
        all_rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return all_rows


def supabase_upsert(table, rows, on_conflict):
    if not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    resp = requests.post(url, headers=SUPABASE_HEADERS, json=rows)
    if resp.status_code not in (200, 201, 204):
        print(f"  Erreur upsert {table} : {resp.status_code} — {resp.text[:300]}")


def purge_old_prices():
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).isoformat()
    resp = requests.delete(
        f"{SUPABASE_URL}/rest/v1/price_history?period_date=lt.{cutoff}",
        headers=SUPABASE_HEADERS,
    )
    if resp.status_code not in (200, 204):
        print(f"Erreur lors de la purge des anciens prix : {resp.status_code} — {resp.text[:300]}")
        return
    print(f"Purge : points de prix antérieurs au {cutoff} supprimés ({RETENTION_DAYS} jours de rétention).")


def run_git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def download_and_publish_json():
    """Fonction 1 : télécharge le fichier officiel Cardmarket et le
    commit/push dans le dépôt — mêmes commandes que les automatisations
    GitHub Actions déjà en place ailleurs sur ce projet (pull --rebase
    avant de pousser, pour éviter un rejet en cas de push concurrent)."""
    print(f"Téléchargement de {PRICE_GUIDE_URL}...")
    resp = get_with_retry(PRICE_GUIDE_URL)
    with open(LOCAL_JSON_PATH, "wb") as f:
        f.write(resp.content)
    print(f"  Enregistré dans {LOCAL_JSON_PATH} ({len(resp.content) / 1_000_000:.1f} Mo).")

    if SKIP_GIT_PUSH:
        print("  SKIP_GIT_PUSH activé — pas de commit/push (utile en test local).")
        return

    code, out, err = run_git("status", "--porcelain", LOCAL_JSON_PATH)
    if not out.strip():
        print("  Aucun changement dans le fichier depuis la dernière fois — rien à pousser.")
        return

    run_git("pull", "--rebase")
    run_git("add", LOCAL_JSON_PATH)
    code, out, err = run_git("commit", "-m", f"Mise à jour price_guide_6.json ({datetime.date.today().isoformat()})")
    if code != 0:
        print(f"  Rien à committer ou erreur : {err}")
        return
    code, out, err = run_git("push")
    if code != 0:
        print(f"  Erreur lors du push : {err}")
    else:
        print("  Fichier poussé sur le dépôt.")


def update_prices_from_json():
    """Fonction 2 : utilise le fichier déjà téléchargé localement pour
    mettre à jour price_history, en rapprochant via card_variants.cardmarket_id."""
    print("\nRécupération des identifiants Cardmarket déjà connus (card_variants)...")
    variants = supabase_get_all("card_variants?cardmarket_id=not.is.null&select=card_id,cardmarket_id")
    cardmarket_to_card = {}
    for v in variants:
        cardmarket_to_card.setdefault(str(v["cardmarket_id"]), v["card_id"])
    print(f"  {len(cardmarket_to_card)} identifiant(s) Cardmarket connu(s) en base.")

    with open(LOCAL_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    guides = data.get("priceGuides", [])
    print(f"  {len(guides)} entrée(s) dans le fichier (Pokémon uniquement, créé le {data.get('createdAt')}).")

    today = datetime.date.today().isoformat()
    rows = []
    matched = 0

    for g in guides:
        if g.get("idCategory") != CARD_CATEGORY_ID:
            continue
        card_id = cardmarket_to_card.get(str(g.get("idProduct")))
        if card_id is None:
            continue

        trend = g.get("trend")
        if trend is None:
            continue

        matched += 1
        rows.append({
            "card_id": card_id,
            "period_date": today,
            "price": trend,
            "min_price": g.get("low"),
            "max_price": None,
            "currency": "EUR",
        })

    print(f"{matched} carte(s) avec un prix trouvé.\n")

    print("Écriture dans price_history par lots...")
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i:i + UPSERT_BATCH_SIZE]
        supabase_upsert("price_history", batch, on_conflict="target_key,period_date,currency")
        print(f"  [{min(i + UPSERT_BATCH_SIZE, len(rows))}/{len(rows)}] écrit(s)...")

    print(f"\nTerminé — {matched} prix mis à jour pour aujourd'hui ({today}).")


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    purge_old_prices()
    download_and_publish_json()
    update_prices_from_json()


if __name__ == "__main__":
    main()
