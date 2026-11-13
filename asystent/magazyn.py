"""Lokalny stan asystenta (katalog dane/): pami�� podr�czna nag��wk�w, listy,
plany, kwarantanna, kolejka zatwierdze� i dziennik dzia�a�."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


def teraz_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bezpieczna_nazwa(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s, flags=re.UNICODE)[:80] or "folder"


_BLOKADA = threading.RLock()


def _z_ponowieniem(f, proby=40):
    """Windows blokuje plik czytany przez inny w�tek/program (antywirus) - ponawiamy."""
    for i in range(proby):
        try:
            return f()
        except PermissionError:
            if i == proby - 1:
                raise
            time.sleep(0.05)


class Magazyn:
    def __init__(self, katalog: Path):
        self.katalog = Path(katalog) / "dane"
        for p in ("cache", "plany"):
            (self.katalog / p).mkdir(parents=True, exist_ok=True)

    # --- narz�dzia plikowe ---
    def _czytaj(self, nazwa: str, domyslnie):
        p = self.katalog / nazwa
        with _BLOKADA:
            if not p.exists():
                return domyslnie
            try:
                return json.loads(_z_ponowieniem(lambda: p.read_text(encoding="utf-8")))
            except Exception:
                return domyslnie

    def _zapisz(self, nazwa: str, dane) -> None:
        p = self.katalog / nazwa
        p.parent.mkdir(parents=True, exist_ok=True)
        tekst = json.dumps(dane, ensure_ascii=False, indent=1)
        with _BLOKADA:
            tmp = p.with_suffix(p.suffix + f".{threading.get_ident()}.tmp")
            tmp.write_text(tekst, encoding="utf-8")
            _z_ponowieniem(lambda: os.replace(tmp, p))

    def _dopisz(self, nazwa: str, rekord: dict) -> None:
        with _BLOKADA:
            def zapis():
                with open(self.katalog / nazwa, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rekord, ensure_ascii=False) + "\n")
            _z_ponowieniem(zapis)

    def _linie(self, nazwa: str) -> list[dict]:
        p = self.katalog / nazwa
        with _BLOKADA:
            if not p.exists():
                return []
            tekst = _z_ponowieniem(lambda: p.read_text(encoding="utf-8"))
        wynik = []
        for linia in tekst.splitlines():
            try:
                wynik.append(json.loads(linia))
            except Exception:
                pass
        return wynik

    # --- cache nag��wk�w ---
    def cache(self, folder: str) -> dict:
        return self._czytaj(f"cache/{_bezpieczna_nazwa(folder)}.json",
                            {"folder": folder, "uidvalidity": 0, "skan": None, "wiadomosci": {}})

    def zapisz_cache(self, folder: str, dane: dict) -> None:
        self._zapisz(f"cache/{_bezpieczna_nazwa(folder)}.json", dane)

    def foldery_w_cache(self) -> list[str]:
        wynik = []
        for p in (self.katalog / "cache").glob("*.json"):
            try:
                wynik.append(json.loads(p.read_text(encoding="utf-8"))["folder"])
            except Exception:
                pass
        return wynik

    # --- korespondenci (z folderu Wys�ane) ---
    def korespondenci(self) -> set[str]:
        return set(self._czytaj("korespondenci.json", []))

    def zapisz_korespondentow(self, adresy) -> None:
        self._zapisz("korespondenci.json", sorted(set(adresy)))

    # --- listy blokowanych / chronionych ---
    def listy(self) -> dict:
        d = self._czytaj("listy.json", {})
        d.setdefault("zablokowani", [])
        d.setdefault("chronieni", [])
        return d

    def zapisz_listy(self, d: dict) -> None:
        self._zapisz("listy.json", d)

    # --- regu�y (edytowalne r�cznie) ---
    def reguly(self) -> list[dict]:
        p = self.katalog.parent / "reguly.yaml"
        if not p.exists():
            return []
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("reguly", []) or []

    def zapisz_reguly(self, reguly: list[dict]) -> None:
        p = self.katalog.parent / "reguly.yaml"
        naglowek = ("# Regu�y automatycznego porz�dkowania.\n"
                    "# Regu�y dodane przez Claude maj� 'zatwierdzona: false' - zatwierdzasz je\n"
                    "# poleceniem 'zatwierdz.cmd' albo zmieniaj�c warto�� r�cznie na true.\n"
                    "# Regu�y NIGDY nie dotycz� wiadomo�ci chronionych i tylko przenosz� (nie usuwaj�).\n")
        p.write_text(naglowek + yaml.safe_dump({"reguly": reguly}, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")

    # --- plany ---
    def nowy_plan(self, dane: dict) -> str:
        pid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        dane = {"id": pid, "utworzono": teraz_iso(), "status": "przygotowany", **dane}
        self._zapisz(f"plany/{pid}.json", dane)
        return pid

    def plan(self, pid: str) -> dict | None:
        if not re.fullmatch(r"[\w-]+", pid or ""):
            return None
        return self._czytaj(f"plany/{pid}.json", None)

    def zapisz_plan(self, plan: dict) -> None:
        self._zapisz(f"plany/{plan['id']}.json", plan)

    # --- kwarantanna ---
    def dodaj_do_kwarantanny(self, wpisy: list[dict]) -> None:
        for w in wpisy:
            self._dopisz("kwarantanna.jsonl", w)

    def kwarantanna(self) -> list[dict]:
        """Zwraca aktualny stan wpis�w (ostatni zapis dla danego message_id wygrywa)."""
        stan: dict[str, dict] = {}
        for w in self._linie("kwarantanna.jsonl"):
            stan[w["message_id"]] = {**stan.get(w["message_id"], {}), **w}
        return list(stan.values())

    def oznacz_kwarantanne(self, message_id: str, **zmiany) -> None:
        self._dopisz("kwarantanna.jsonl", {"message_id": message_id, **zmiany})

    # --- kolejka zatwierdze� (wypisania, regu�y) ---
    def kolejka(self) -> list[dict]:
        return self._czytaj("kolejka_zatwierdzen.json", [])

    def zapisz_kolejke(self, k: list[dict]) -> None:
        self._zapisz("kolejka_zatwierdzen.json", k)

    # --- dziennik ---
    def dziennik(self, akcja: str, **szczegoly) -> None:
        self._dopisz("dziennik.jsonl", {"czas": teraz_iso(), "akcja": akcja, **szczegoly})

    def wpisy_dziennika(self, ile: int = 50) -> list[dict]:
        return self._linie("dziennik.jsonl")[-ile:]
