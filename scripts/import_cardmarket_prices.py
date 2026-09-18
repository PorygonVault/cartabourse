"""
Collecte des prix Cardmarket via le fichier price guide officiel
CartaBourse

Utilise le fichier officiel de Cardmarket, avec le prix "trend" (mis à
jour chaque jour), stocké dans une colonne "price" volontairement
neutre — si la méthode de calcul change encore un jour, pas besoin de
renommer la colonne à nouveau.

Fait trois choses, dans l'ordre, à chaque exécution :
  1. Télécharge le fichier price_guide_6.json depuis Cardmarket, et le
     commit/push dans le dépôt Git (utile pour l'historique, le debug,
     et pour que d'autres outils du dépôt puissent s'en servir sans
     retélécharger) — mêmes commandes git que vos automatisations
     GitHub Actions existantes (ebay-daily.yml, etc.)
  2. Écrit dans price_history une ligne par carte individuelle
     (idCategory=51) présente dans le fichier :
       - si son cardmarket_id est déjà relié à une carte de la base
         (card_variants.cardmarket_id) → ligne "carte" normale
         (card_id renseigné) ;
       - sinon → ligne "non reliée" (cardmarket_id renseigné, card_id
         et product_id restent NULL). Le but : dès qu'un cardmarket_id
         est relié à une carte plus tard (cross-référencement manuel,
         futur import), tout l'historique déjà accumulé est récupérable
         d'un coup — voir backfill-cardmarket-id-vers-price-history.sql.
     Nécessite la migration migration-price-history-cardmarket-id.sql
     (colonne cardmarket_id + target_key à 3 branches).
  3. Purge automatique : comme avant, les points de plus de 2 semaines
     sont supprimés, mais UNIQUEMENT pour les lignes déjà reliées à une
     carte ou un produit — les lignes "non reliées" (cardmarket_id
     seul) ne sont jamais purgées : elles doivent survivre jusqu'à ce
     qu'on sache à quelle carte elles correspondent.

Prérequis :
  1. migration-price-history-cardmarket-id.sql exécuté dans Supabase
  2. pip install requests
  3. Lancé depuis un dépôt Git déjà cloné (avec les identifiants git
     configurés AVANT ce script — voir cardmarket-daily.yml)

Utilisation :
  SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_KEY=... \
  python import_cardmarket_prices.py
"""

try:
    from dotenv import load_dotenv
    load_dotenv()  # charge .env en local si présent — absent en CI (GitHub Actions fournit déjà les variables), ce n'est pas un problème
except ImportError:
    pass  # python-dotenv non installé (ex. GitHub Actions) — les variables d'environnement sont déjà fournies autrement

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
    """Renvoie True si le lot a bien été écrit, False sinon — pour que
    l'appelant puisse compter les échecs réels plutôt que de supposer
    que tout s'est bien passé dès que la requête part sans exception."""
    if not rows:
        return True
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    try:
        resp = requests.post(url, headers=SUPABASE_HEADERS, json=rows, timeout=120)
    except requests.exceptions.RequestException as e:
        print(f"  Erreur réseau upsert {table} : {e}")
        return False
    if resp.status_code not in (200, 201, 204):
        print(f"  Erreur upsert {table} : {resp.status_code} — {resp.text[:300]}")
        return False
    return True


def purge_old_prices():
    # Uniquement les lignes deja reliees (card_id ou product_id) : les
    # lignes "non reliees" (cardmarket_id seul) ne sont jamais purgees,
    # elles doivent survivre jusqu'a ce qu'une carte leur soit associee.
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).isoformat()
    resp = requests.delete(
        f"{SUPABASE_URL}/rest/v1/price_history?period_date=lt.{cutoff}&cardmarket_id=is.null",
        headers=SUPABASE_HEADERS,
    )
    if resp.status_code not in (200, 204):
        print(f"Erreur lors de la purge des anciens prix : {resp.status_code} — {resp.text[:300]}")
        return
    print(f"Purge : points de prix (cartes/produits déjà reliés) antérieurs au {cutoff} supprimés "
          f"({RETENTION_DAYS} jours de rétention — les lignes non reliées ne sont jamais purgées).")


def run_git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def download_and_publish_json():
    """Fonction 1 : télécharge le fichier officiel Cardmarket et le
    commit/push dans le dépôt — mêmes commandes que les automatisations
    GitHub Actions déjà en place ailleurs sur ce projet.

    Ordre important : on se synchronise avec le dépôt distant AVANT
    d'écrire le fichier localement — un pull --rebase avec une
    modification locale déjà présente (mais non commitée) risquerait
    d'échouer ou de mal se comporter en cas de changement distant entre-temps.

    Nécessite que git config user.name/user.email soient déjà définis
    AVANT l'appel à ce script (voir cardmarket-daily.yml) — sans ça, le
    commit ci-dessous échoue silencieusement (le fichier est bien
    téléchargé et utilisé pour la suite, mais jamais persisté dans le
    dépôt)."""
    if not SKIP_GIT_PUSH:
        run_git("pull", "--rebase")

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
    mettre à jour price_history — en ligne "carte" (card_id) pour les
    cardmarket_id déjà reliés, en ligne "non reliée" (cardmarket_id
    seul) pour les autres, afin de garder leur historique prêt pour le
    jour où elles seront reliées."""
    print("\nRécupération des identifiants Cardmarket déjà connus (card_variants)...")
    variants = supabase_get_all("card_variants?cardmarket_id=not.is.null&select=card_id,cardmarket_id,variant_id")

    # Une même carte a souvent plusieurs variantes (normal, reverse holo...),
    # donc plusieurs cardmarket_id distincts qui pointent vers le même
    # card_id. price_history n'a qu'une ligne par carte et par jour (pas de
    # notion de variante) : s'il fallait écrire un prix par variante, deux
    # lignes de la même écriture porteraient le même target_key ("c:<id>"),
    # ce que Postgres refuse ("ON CONFLICT DO UPDATE command cannot affect
    # row a second time"). On ne retient donc qu'UN SEUL cardmarket_id par
    # carte — celui de la variante "normal" en priorité, sinon le premier
    # rencontré — pour le prix "carte". Les autres variantes de la même
    # carte restent des lignes "non reliées" (cardmarket_id seul), ce qui
    # ne perd rien : leur historique reste disponible sous leur propre
    # cardmarket_id.
    card_to_preferred_cardmarket = {}
    for v in variants:
        card_id = v["card_id"]
        is_normal = v.get("variant_id") == "normal"
        current = card_to_preferred_cardmarket.get(card_id)
        if current is None or (is_normal and not current[1]):
            card_to_preferred_cardmarket[card_id] = (str(v["cardmarket_id"]), is_normal)

    cardmarket_to_card = {
        cm_id: card_id
        for card_id, (cm_id, _is_normal) in card_to_preferred_cardmarket.items()
    }
    print(f"  {len(variants)} variante(s) avec cardmarket_id, {len(cardmarket_to_card)} carte(s) unique(s) "
          f"retenue(s) pour le prix \"carte\" (1 par carte, variante normale privilégiée).")

    with open(LOCAL_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    guides = data.get("priceGuides", [])
    print(f"  {len(guides)} entrée(s) dans le fichier (créé le {data.get('createdAt')}).")

    today = datetime.date.today().isoformat()
    rows = []
    matched = 0
    unmatched = 0

    for g in guides:
        if g.get("idCategory") != CARD_CATEGORY_ID:
            continue
        trend = g.get("trend")
        if trend is None:
            continue

        id_product = g.get("idProduct")
        card_id = cardmarket_to_card.get(str(id_product))

        # PostgREST exige que tous les objets d'un même envoi en lot aient
        # exactement les mêmes clés ("All object keys must match" / PGRST102) —
        # donc toujours les deux clés ici, avec None pour celle qui ne
        # s'applique pas, plutôt que de l'omettre selon le cas.
        row = {
            "period_date": today,
            "price": trend,
            "min_price": g.get("low"),
            "max_price": None,
            "currency": "EUR",
            "card_id": card_id,
            "cardmarket_id": None if card_id is not None else id_product,
        }
        if card_id is not None:
            matched += 1
        else:
            unmatched += 1
        rows.append(row)

    # Garde-fou : même en théorie impossible désormais, on déduplique par
    # target_key (même logique que la colonne générée en base) avant
    # l'envoi, pour ne jamais reproduire l'erreur Postgres "ON CONFLICT DO
    # UPDATE command cannot affect row a second time" si une autre source
    # de doublon apparaissait un jour (ex. idProduct dupliqué dans le
    # fichier Cardmarket lui-même).
    rows_by_target = {}
    for row in rows:
        key = f"c:{row['card_id']}" if row["card_id"] is not None else f"cm:{row['cardmarket_id']}"
        rows_by_target[key] = row
    if len(rows_by_target) != len(rows):
        print(f"  ({len(rows) - len(rows_by_target)} doublon(s) de target_key supprimé(s) avant l'envoi.)")
    rows = list(rows_by_target.values())

    print(f"{matched + unmatched} produit(s) individuel(s) avec un prix dans le fichier : "
          f"{matched} déjà reliés à une carte, {unmatched} pas encore reliés.\n")

    print("Écriture dans price_history...")
    failed_batches = 0
    failed_rows = 0
    written_rows = 0
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i:i + UPSERT_BATCH_SIZE]
        ok = supabase_upsert("price_history", batch, on_conflict="target_key,period_date,currency")
        if ok:
            written_rows += len(batch)
            print(f"  [{min(i + UPSERT_BATCH_SIZE, len(rows))}/{len(rows)}] écrit(s)...")
        else:
            failed_batches += 1
            failed_rows += len(batch)
            print(f"  [{min(i + UPSERT_BATCH_SIZE, len(rows))}/{len(rows)}] ÉCHEC de ce lot "
                  f"({len(batch)} ligne(s) non écrite(s)).")

    if failed_batches:
        print(f"\nTerminé avec des ERREURS — {failed_batches} lot(s) sur "
              f"{(len(rows) + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE} ont échoué "
              f"({failed_rows} ligne(s) non écrite(s) sur {len(rows)}). "
              f"{written_rows} ligne(s) bien écrite(s) — relancer le script réécrira les lots manquants "
              f"(l'upsert est idempotent), pour aujourd'hui ({today}).")
    else:
        print(f"\nTerminé — {matched} carte(s) mise(s) à jour, {unmatched} en attente de rattachement, "
              f"{written_rows} ligne(s) écrite(s) au total, pour aujourd'hui ({today}).")


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    purge_old_prices()
    download_and_publish_json()
    update_prices_from_json()


if __name__ == "__main__":
    main()
