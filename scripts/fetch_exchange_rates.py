"""
Récupération du taux de change EUR/GBP — CartaBourse

Contrairement à la version précédente (le navigateur du visiteur appelait
directement une API externe), ce script tourne côté serveur et enregistre
le taux dans Supabase — le site n'a plus qu'à lire cette table, ce qui
évite au passage le blocage CORS déjà rencontré (un script Python n'est
jamais concerné par cette restriction, propre aux navigateurs).

Plusieurs sources tentées dans l'ordre (comme côté site) : si l'une est
indisponible, la suivante prend le relais, avec un taux de secours fixe
en tout dernier recours pour ne jamais échouer complètement.

Prérequis :
  1. creation-exchange-rates.sql exécuté dans Supabase
  2. pip install requests

Utilisation :
  SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_KEY=... \
  python fetch_exchange_rates.py
"""

import os
import sys
import time
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

FALLBACK_EUR_TO_GBP = 0.87  # à réviser de temps en temps si les sources en ligne deviennent indisponibles durablement

SOURCES = [
    {"url": "https://api.frankfurter.app/latest?from=EUR&to=GBP", "extract": lambda d: d["rates"]["GBP"]},
    {"url": "https://open.er-api.com/v6/latest/EUR", "extract": lambda d: d["rates"]["GBP"]},
]

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    sys.exit("Erreur : SUPABASE_URL et SUPABASE_SERVICE_KEY doivent être définies en variables d'environnement.")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=representation",
}


def fetch_rate():
    for source in SOURCES:
        try:
            resp = requests.get(source["url"], timeout=15)
            if not resp.ok:
                continue
            rate = source["extract"](resp.json())
            if rate:
                print(f"Taux récupéré via {source['url']} : {rate}")
                return rate
        except (requests.exceptions.RequestException, KeyError, ValueError) as e:
            print(f"Source indisponible ({source['url']}) : {e}")
            continue
    print("Aucune source en ligne joignable — utilisation du taux de secours fixe.")
    return FALLBACK_EUR_TO_GBP


def main():
    rate = fetch_rate()

    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/exchange_rates?on_conflict=base_currency,target_currency",
        headers=SUPABASE_HEADERS,
        json=[{
            "base_currency": "EUR",
            "target_currency": "GBP",
            "rate": rate,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    )
    if resp.status_code not in (200, 201):
        sys.exit(f"Erreur d'enregistrement dans Supabase : {resp.status_code} — {resp.text[:300]}")

    print(f"Enregistré : 1 EUR = {rate} GBP")


if __name__ == "__main__":
    main()
