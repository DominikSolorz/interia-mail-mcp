"""Konfiguracja: plik konfiguracja.yaml + has�o w Mened�erze po�wiadcze� Windows."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

USLUGA_KEYRING = "asystent-pocztowy-interia"

DOMYSLNA = {
    "konto": {
        "email": "",
        "imap_host": "poczta.interia.pl",
        "imap_port": 993,
        "smtp_host": "poczta.interia.pl",
        "smtp_port": 465,
    },
    "foldery": {
        "kosz": "Kosz asystenta",          # w�asny kosz - Interia go NIE opr��nia (pewne cofanie)
        "kwarantanna": "Asystent-Kwarantanna",
        "archiwum": "Asystent-Archiwum",
        "nowi_nadawcy": "Asystent-Nowi-nadawcy",
    },
    "zasady": {
        "kwarantanna_dni": 30,
        "reklama_starsze_niz_dni": 30,
        "newsletter_starsze_niz_dni": 60,
        "powiadomienia_starsze_niz_dni": 30,
        "spolecznosciowe_starsze_niz_dni": 14,
        "transakcyjne_archiwum_po_dniach": 180,
        "nieaktywna_subskrypcja_min_maili": 5,
        "plan_wazny_godzin": 24,
        "screener": False,
    },
    "ochrona": {
        "dodatkowe_domeny": [],
        "dodatkowe_slowa": [],
    },
    "ai": {
        "wlaczone": False,
        "adres_api": "",
        "model": "",
        "model_weryfikacji": "",
        "maks_grup": 150,
    },
    "panel": {
        "port": 8765,
        "auto_czyszczenie": False,
        "auto_co_minut": 60,
    },
}


def katalog_projektu() -> Path:
    return Path(__file__).resolve().parent.parent


def _scal(baza: dict, nad: dict) -> dict:
    wynik = copy.deepcopy(baza)
    for k, v in (nad or {}).items():
        if isinstance(v, dict) and isinstance(wynik.get(k), dict):
            wynik[k] = _scal(wynik[k], v)
        else:
            wynik[k] = v
    return wynik


def wczytaj(katalog: Path | None = None) -> dict:
    katalog = katalog or katalog_projektu()
    plik = katalog / "konfiguracja.yaml"
    dane = {}
    if plik.exists():
        dane = yaml.safe_load(plik.read_text(encoding="utf-8")) or {}
    return _scal(DOMYSLNA, dane)


def zapisz(cfg: dict, katalog: Path | None = None) -> Path:
    katalog = katalog or katalog_projektu()
    plik = katalog / "konfiguracja.yaml"
    plik.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return plik


def haslo(email: str) -> str | None:
    sekret = os.environ.get("INTERIA_PASSWORD")
    if sekret:
        return sekret
    import keyring
    return keyring.get_password(USLUGA_KEYRING, email)


def ustaw_haslo(email: str, wartosc: str) -> None:
    import keyring
    keyring.set_password(USLUGA_KEYRING, email, wartosc)
