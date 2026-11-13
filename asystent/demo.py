"""Przyk�adowa skrzynka do pokazu panelu (nie ��czy si� z Interi�)."""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import konfiguracja

BULK = {"list-unsubscribe": "<https://example.invalid/u>, <mailto:unsubscribe@example.invalid>",
        "list-unsubscribe-post": "List-Unsubscribe=One-Click", "precedence": "bulk"}

NADAWCY = [
    # (nazwa, adres, tematy, liczba, rodzaj, odsetek_przeczytanych, co_ile_dni)
    ("Pracuj.pl", "oferty@pracuj.pl", ["Nowe oferty pracy dla Ciebie", "10 ofert pasuj�cych do Twojego profilu",
                                       "Pracodawcy szukaj� takich os�b jak Ty"], 120, "news", 0.05, 3),
    ("Allegro", "allegro@allegro.pl", ["Black Friday - rabaty do -70%", "Tylko dzi�: darmowa dostawa",
                                        "Okazje tygodnia dla Ciebie", "Kod rabatowy -20 z�"], 95, "promo", 0.1, 4),
    ("Allegro", "powiadomienia@allegro.pl", ["Twoje zam�wienie zosta�o wys�ane", "Przesy�ka czeka w automacie"],
     14, "trans", 0.9, 25),
    ("OLX", "powiadomienia@olx.pl", ["Nowe og�oszenia w obserwowanych", "Kto� wy�wietli� Twoje og�oszenie",
                                      "Promocja: wyr��nij og�oszenie taniej"], 80, "news", 0.08, 5),
    ("Zalando", "info@zalando.pl", ["Nowa kolekcja ju� jest", "Wyprzeda� -50%", "Ostatnia szansa!"], 60, "promo", 0.0, 6),
    ("Biedronka", "gazetka@biedronka.pl", ["Nowa gazetka od czwartku", "Promocje tygodnia"], 55, "promo", 0.15, 7),
    ("Temu", "temu@eu.temuemail.com", ["Prezent czeka na Ciebie", "Gratis dla nowych", "-90% tylko dzi�"], 70,
     "promo", 0.0, 3),
    ("Booking.com", "email.campaign@sg.booking.com", ["Okazje na weekend", "Rabat 15% na pobyt"], 30, "promo", 0.05, 12),
    ("Facebook", "notification@facebookmail.com", ["Masz 5 nowych powiadomie�", "Anna doda�a zdj�cie"], 45,
     "social", 0.2, 6),
    ("Onet Newsletter", "newsletter@onet.pl", ["Najwa�niejsze wiadomo�ci dnia", "Poranny przegl�d"], 40, "news", 0.0, 4),
    ("Spam", "winner@lucky-prize.biz", ["Wygraj iPhone 17!", "Gratulacje, zosta�e� wybrany"], 18, "spam", 0.0, 9),
    ("mBank", "kontakt@mbank.pl", ["Wyci�g z rachunku za pa�dziernik", "Nowa promocja kredytu"], 12, "bank", 0.8, 30),
    ("e-Urz�d Skarbowy", "powiadomienia@podatki.gov.pl", ["Nowe pismo w e-Urz�dzie", "PIT-37 - potwierdzenie"],
     4, "urzad", 1.0, 90),
    ("PGE Obr�t", "efaktura@pge.pl", ["Twoja e-faktura za energi�", "Faktura nr 12/2026"], 12, "faktura", 0.9, 30),
    ("Kasia", "kasia.nowak@gmail.com", ["Niedzielny obiad?", "Zdj�cia z wakacji", "Wszystkiego najlepszego!"],
     9, "prywatne", 1.0, 40),
    ("Orange", "info@orange.pl", ["Twoja faktura Orange", "Nowa oferta �wiat�owodu"], 10, "news", 0.4, 30),
]


class SkrzynkaDemo:
    def __init__(self):
        self.foldery_ = {"INBOX": {}, "Wys�ane": {}, "Kosz": {}, "Spam": {}}
        self.uv = 4242
        self.mozliwosci = {"MOVE", "UIDPLUS"}
        self.nast = 100000
        los = random.Random(7)
        teraz = datetime.now(timezone.utc)
        uid = 1
        for nazwa, adres, tematy, n, rodzaj, czyt, co in NADAWCY:
            for i in range(n):
                dni = i * co + los.random() * co
                naglowki = {}
                if rodzaj in ("news", "promo", "social", "bank"):
                    naglowki = dict(BULK)
                if rodzaj == "spam":
                    naglowki = {"x-spam-flag": "YES"}
                temat = tematy[i % len(tematy)]
                rec = {"uid": uid, "message_id": f"<demo{uid}@{adres.split('@')[1]}>", "od": adres,
                       "od_nazwa": nazwa, "do": ["demo@interia.pl"], "temat": temat,
                       "data": (teraz - timedelta(days=dni)).isoformat(), "przeczytana": los.random() < czyt,
                       "oznaczona": False, "rozmiar": 20000,
                       "zalaczniki": ["pdf"] if rodzaj == "faktura" else [], "n": naglowki}
                folder = "Spam" if rodzaj == "spam" and i % 2 else "INBOX"
                self.foldery_[folder][uid] = rec
                uid += 1
        self.foldery_["Wys�ane"][uid] = {"uid": uid, "message_id": "<s1@demo>", "od": "demo@interia.pl",
                                         "od_nazwa": "", "do": ["kasia.nowak@gmail.com"], "temat": "Re: obiad",
                                         "data": teraz.isoformat(), "przeczytana": True, "oznaczona": False,
                                         "rozmiar": 1000, "zalaczniki": [], "n": {}}

    def __enter__(self):
        return self

    def __exit__(self, *e):
        pass

    def foldery(self):
        fl = {"Wys�ane": ["\\Sent"], "Kosz": ["\\Trash"], "Spam": ["\\Junk"]}
        return [{"nazwa": n, "flagi": fl.get(n, []), "separator": "/"} for n in self.foldery_]

    def utworz_folder(self, n):
        self.foldery_.setdefault(n, {})

    def wybierz(self, folder, tylko_odczyt=True):
        return len(self.foldery_.get(folder, {})), self.uv

    def uidy(self, folder):
        return self.uv, sorted(self.foldery_.get(folder, {}))

    def flagi(self, folder):
        return {u: (["\\Seen"] if m["przeczytana"] else []) for u, m in self.foldery_[folder].items()}

    def naglowki(self, folder, uidy, partia=250, tylko_adresaci=False):
        for u in uidy:
            yield dict(self.foldery_[folder][u])

    def szukaj_message_id(self, folder, mid):
        return [u for u, m in self.foldery_.get(folder, {}).items() if m["message_id"] == mid]

    def przenies(self, folder, uidy, cel):
        for u in uidy:
            m = self.foldery_[folder].pop(u)
            self.nast += 1
            self.foldery_.setdefault(cel, {})[self.nast] = {**m, "uid": self.nast}
        return len(uidy)

    def cala_wiadomosc(self, folder, uid, maks_bajtow=0):
        m = self.foldery_[folder].get(int(uid))
        if not m:
            return None
        return (f"From: {m['od_nazwa']} <{m['od']}>\r\nSubject: {m['temat']}\r\nDate: {m['data']}\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n\r\nTo jest przyk�adowa tre�� wiadomo�ci "
                f""{m['temat']}" w trybie demonstracyjnym.").encode("utf-8")


def przygotuj_demo():
    katalog = Path(tempfile.mkdtemp(prefix="asystent-demo-"))
    cfg = konfiguracja.wczytaj(katalog)
    cfg["konto"]["email"] = "demo@interia.pl"
    konfiguracja.zapisz(cfg, katalog)
    skrzynka = SkrzynkaDemo()
    return katalog, (lambda: skrzynka)
