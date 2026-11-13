"""Polecenia wykonywane przez CZ�OWIEKA w lokalnym terminalu.

Te polecenia wymagaj� interaktywnego terminala (nie da si� ich uruchomi�
automatycznie z innego programu ani przez AI) - to Twoja "r�ka na hamulcu".
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections import Counter

from . import konfiguracja
from .imap import BladImap
from .uslugi import Asystent, BladAsystenta


def _wymagaj_terminala():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("To polecenie wymaga interaktywnego terminala i potwierdzenia przez cz�owieka.")
        sys.exit(2)


def _pytaj(tekst: str) -> str:
    try:
        return input(tekst).strip()
    except EOFError:
        return ""


def konfiguruj(_args):
    _wymagaj_terminala()
    cfg = konfiguracja.wczytaj()
    print("Konfiguracja asystenta pocztowego Interia\n")
    print("Najpierw w��cz IMAP w poczcie Interia: Ustawienia  Ustawienia g��wne  Parametry ")
    print(""korzystam z programu pocztowego" = TAK.\n")
    email = _pytaj(f"Adres e-mail [{cfg['konto']['email']}]: ") or cfg["konto"]["email"]
    if "@" not in email:
        print("Niepoprawny adres.")
        sys.exit(1)
    cfg["konto"]["email"] = email
    konfiguracja.zapisz(cfg)
    haslo = getpass.getpass("Has�o do poczty (nie b�dzie widoczne, trafi do Mened�era po�wiadcze� Windows): ")
    if haslo:
        konfiguracja.ustaw_haslo(email, haslo)
    print("\nZapisano. Sprawdzam po��czenie")
    test(None)


def test(_args):
    try:
        s = Asystent().status()
    except (BladAsystenta, BladImap) as e:
        print(f"B��D: {e}")
        sys.exit(1)
    print(f"Po��czono z {s['serwer']} jako {s['konto']} (MOVE: {s['move']}, UIDPLUS: {s['uidplus']})")
    for nazwa, n in s["foldery"].items():
        print(f"  {nazwa:<35} {n if n is not None else '-'}")
    print("Foldery specjalne:", s["specjalne"])


def zatwierdz(_args):
    _wymagaj_terminala()
    a = Asystent()
    # 1. regu�y
    reguly = a.reguly()
    zmiana = False
    for r in reguly:
        if r.get("zatwierdzona"):
            continue
        print(f"\nREGU�A "{r['nazwa']}": je�li {r.get('warunek')}  {r['akcja']}")
        odp = _pytaj("Zatwierdzi� t� regu��? [t = tak / n = nie / u = usu�]: ").lower()
        if odp == "t":
            r["zatwierdzona"] = True
            zmiana = True
            a.mag.dziennik("regula_zatwierdzona", nazwa=r["nazwa"])
        elif odp == "u":
            r["_usun"] = True
            zmiana = True
    if zmiana:
        a.mag.zapisz_reguly([r for r in reguly if not r.get("_usun")])
    # 2. wypisania
    kolejka = a.mag.kolejka()
    oczekujace = [q for q in kolejka if q["status"] == "oczekuje"]
    if not oczekujace:
        print("\nBrak wypisa� czekaj�cych na zatwierdzenie.")
    for q in oczekujace:
        m = q["metoda"]
        cel = (m.get("https") or m.get("mailto") or ["-"])[0]
        print(f"\nWYPISANIE: {q['nazwa']} <{q['adres']}> - {q['liczba']} wiadomo�ci")
        print(f"  metoda: {m['rodzaj']}   adres wypisania: {cel}")
        if m["rodzaj"] == "link-reczny":
            print("  Ten nadawca ma tylko zwyk�y link. Je�li chcesz, otw�rz go SAM w przegl�darce.")
        odp = _pytaj("Wypisa�? [t = tak / n = nie, odrzu� / p = pomi� na razie]: ").lower()
        if odp == "t":
            ok, info = a.wykonaj_wypisanie(q)
            q["status"] = "wykonane" if ok else "nieudane"
            q["wynik"] = info
            print(("  ? " if ok else "  ? ") + info)
        elif odp == "n":
            q["status"] = "odrzucone"
        a.mag.zapisz_kolejke(kolejka)
    print("\nGotowe.")


def usun_z_kwarantanny(args):
    _wymagaj_terminala()
    a = Asystent()
    dni = a.cfg["zasady"]["kwarantanna_dni"]
    wpisy = [w for w in a.mag.kwarantanna() if w.get("status") == "w_kwarantannie"]
    if not args.wszystkie:
        from datetime import datetime, timedelta, timezone
        granica = (datetime.now(timezone.utc) - timedelta(days=dni)).isoformat()
        wpisy = [w for w in wpisy if w["przeniesiono"] < granica]
    if not wpisy:
        print(f"Brak wiadomo�ci do usuni�cia (w kwarantannie d�u�ej ni� {dni} dni).")
        return
    kosz = a.foldery_specjalne().get("kosz")
    print(f"Do usuni�cia z kwarantanny: {len(wpisy)} wiadomo�ci. Najcz�stsi nadawcy:")
    for od, n in Counter(w["od"] for w in wpisy).most_common(20):
        print(f"  {n:>5}  {od}")
    if args.trwale:
        print("\nUWAGA: tryb TRWA�Y - wiadomo�ci zostan� usuni�te bezpowrotnie.")
        if _pytaj("Wpisz dok�adnie USUN TRWALE, aby potwierdzi�: ") != "USUN TRWALE":
            print("Anulowano.")
            return
    else:
        if not kosz:
            print("Nie znaleziono folderu Kosz - przerwano.")
            return
        print(f"\nWiadomo�ci zostan� przeniesione do folderu "{kosz}" (tam opr��nisz je w Interii).")
        if _pytaj("Wpisz TAK, aby potwierdzi�: ") != "TAK":
            print("Anulowano.")
            return
    folder_k = a.cfg["foldery"]["kwarantanna"]
    zrobione = 0
    with a.klient() as k:
        uidy, mids = [], []
        for w in wpisy:
            u = k.szukaj_message_id(folder_k, w["message_id"])
            if u:
                uidy.append(u[0])
                mids.append(w["message_id"])
        if args.trwale:
            zrobione = k.usun_trwale(folder_k, uidy)
        else:
            zrobione = k.przenies(folder_k, uidy, kosz)
    for mid in mids:
        a.mag.oznacz_kwarantanne(mid, status="usunieta_trwale" if args.trwale else "w_koszu")
    a.mag.dziennik("kwarantanna_oprozniona", liczba=zrobione, trwale=args.trwale, potwierdzil="uzytkownik")
    print(f"Gotowe: {zrobione} wiadomo�ci {'usuni�to trwale' if args.trwale else 'przeniesiono do Kosza'}.")


def reguly_auto(args):
    """Do harmonogramu: stosuje tylko ZATWIERDZONE regu�y (wy��cznie przenoszenie, chronione pomijane)."""
    a = Asystent()
    try:
        a.skanuj(["INBOX"])
        wynik = a.zastosuj_reguly("INBOX", tylko_zatwierdzone=True)
        if wynik.get("plan_id") and args.wykonaj:
            print(a.wykonaj_plan(wynik["plan_id"]))
        else:
            print(wynik)
    except (BladAsystenta, BladImap) as e:
        print(f"B��D: {e}")
        sys.exit(1)


def panel(args):
    from . import panel as p
    if args.demo:
        from .demo import przygotuj_demo
        katalog, fabryka = przygotuj_demo()
        p.uruchom(args.port or 8799, otworz=not args.bez_przegladarki, katalog=katalog, fabryka=fabryka)
    else:
        p.uruchom(args.port, otworz=not args.bez_przegladarki)


def main(argv=None):
    p = argparse.ArgumentParser(prog="asystent", description="Asystent pocztowy Interia")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("konfiguruj").set_defaults(f=konfiguruj)
    sub.add_parser("test").set_defaults(f=test)
    sub.add_parser("zatwierdz").set_defaults(f=zatwierdz)
    u = sub.add_parser("usun-z-kwarantanny")
    u.add_argument("--wszystkie", action="store_true", help="tak�e m�odsze ni� okres przechowywania")
    u.add_argument("--trwale", action="store_true", help="usu� bezpowrotnie zamiast do Kosza")
    u.set_defaults(f=usun_z_kwarantanny)
    pn = sub.add_parser("panel")
    pn.add_argument("--port", type=int)
    pn.add_argument("--bez-przegladarki", action="store_true")
    pn.add_argument("--demo", action="store_true", help="przyk�adowa skrzynka do pokazu (bez Interii)")
    pn.set_defaults(f=panel)
    r = sub.add_parser("reguly")
    r.add_argument("--wykonaj", action="store_true")
    r.set_defaults(f=reguly_auto)
    args = p.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
