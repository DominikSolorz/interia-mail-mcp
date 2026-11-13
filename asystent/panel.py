"""Graficzny panel porz�dkowania poczty - lokalny serwer WWW (tylko 127.0.0.1).

Ka�da operacja zmieniaj�ca skrzynk� jest wywo�ywana klikni�ciem cz�owieka
i potwierdzana w oknie dialogowym. Usuwanie = przeniesienie do Kosza Interii
(odwracalne). Wiadomo�ci chronione s� pomijane zawsze.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import webbrowser
from collections import Counter
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ai as ai_mod
from . import konfiguracja, wypisywanie
from .imap import BladImap, Klient
from .klasyfikator import KATEGORIE, KWARANTANNA, ARCHIWUM, ZACHOWAJ, domena, domena_glowna, normalizuj
from .uslugi import AKCJE_PANELU, Asystent, BladAsystenta

WWW = Path(__file__).resolve().parent / "panel_www"


class Stan:
    def __init__(self, katalog: Path | None = None, fabryka=None):
        self.katalog = katalog
        self.token: str | None = None
        self.haslo: str | None = None
        self.fabryka = fabryka            # do test�w
        self.skan = {"w_toku": False, "folder": "", "zrobione": 0, "razem": 0, "blad": None, "koniec": None}
        self.blokada = threading.Lock()
        self.serwer: ThreadingHTTPServer | None = None
        self.ostatnie_auto = 0.0
        self.memo = None
        self.blokada_ocen = threading.Lock()
        self.ai_zadanie = {"w_toku": False, "zrobione": 0, "razem": 0, "blad": None, "wynik": None}

    # --- asystent z has�em z pami�ci lub Mened�era po�wiadcze� ---
    def asystent(self) -> Asystent:
        if self.fabryka:
            return Asystent(self.katalog, fabryka_klienta=self.fabryka)
        cfg = konfiguracja.wczytaj(self.katalog)
        k = cfg["konto"]
        haslo = self.haslo or konfiguracja.haslo(k["email"])
        fabryka = (lambda: Klient(k["imap_host"], int(k["imap_port"]), k["email"], haslo)) if haslo else None
        return Asystent(self.katalog, fabryka_klienta=fabryka)

    def foldery(self, a: Asystent) -> list[str]:
        """Zwraca wszystkie zeskanowane foldery poza Wysłanymi i systemowym Koszem."""
        spec = a.foldery_specjalne()
        wykluczone = {x for x in (spec.get("wyslane"), spec.get("kosz")) if x}
        return [f for f in a.mag.foldery_w_cache() if f not in wykluczone]

    # --- skan w tle ---
    def uruchom_skan(self, po_skanie_auto: bool = False):
        with self.blokada:
            if self.skan["w_toku"]:
                return False
            self.skan.update(w_toku=True, blad=None, zrobione=0, razem=0, folder="")

        def praca():
            try:
                a = self.asystent()
                a.skanuj(postep=lambda **kw: self.skan.update(**kw))
                if po_skanie_auto and a.cfg["panel"].get("auto_czyszczenie"):
                    self.auto_czyszczenie(a)
            except Exception as e:  # noqa: BLE001
                self.skan["blad"] = str(e)
                traceback.print_exc()
            finally:
                self.skan.update(w_toku=False, koniec=time.time())
        threading.Thread(target=praca, daemon=True).start()
        return True

    def auto_czyszczenie(self, a: Asystent) -> dict:
        wynik = {"przeniesiono": 0}
        for folder in self.foldery(a):
            plan = a.zastosuj_reguly(folder, tylko_zatwierdzone=True)
            if plan.get("plan_id"):
                w = a.wykonaj_plan(plan["plan_id"])
                wynik["przeniesiono"] += sum(w["przeniesiono"].values())
        a.mag.dziennik("auto_czyszczenie", **wynik)
        self.ostatnie_auto = time.time()
        return wynik

    def petla_auto(self):
        while True:
            time.sleep(60)
            try:
                a = self.asystent()
                co = int(a.cfg["panel"].get("auto_co_minut", 60)) * 60
                if a.cfg["panel"].get("auto_czyszczenie") and self.token and \
                        time.time() - self.ostatnie_auto > co and not self.skan["w_toku"]:
                    self.ostatnie_auto = time.time()
                    self.uruchom_skan(po_skanie_auto=True)
            except Exception:
                traceback.print_exc()


# ---------------------------------------------------------------------- logika API
def _sygnatura(a: Asystent) -> tuple:
    pliki = sorted((a.mag.katalog / "cache").glob("*.json")) + [
        a.mag.katalog / "listy.json", a.mag.katalog / "korespondenci.json", a.katalog / "konfiguracja.yaml"]
    return tuple((p.name, p.stat().st_mtime_ns if p.exists() else 0) for p in pliki) + (int(time.time() // 3600),)


def _oceny_wszystkie(s: Stan, a: Asystent):
    """Oceny wszystkich wiadomo�ci - liczone raz i pami�tane do zmiany plik�w (szybki panel)."""
    sygn = _sygnatura(a)
    with s.blokada_ocen:
        if s.memo and s.memo[0] == sygn:
            return s.memo[1]
        wynik = _licz_oceny(s, a)
        s.memo = (sygn, wynik)
        return wynik


def _licz_oceny(s: Stan, a: Asystent):
    wszystkie = []
    for folder in s.foldery(a):
        try:
            _, oceny = a._oceny(folder)
        except BladAsystenta:
            continue
        for rec, o, ak, uz in oceny:
            rec["_folder"] = folder
            wszystkie.append((rec, o, ak, uz))
    return wszystkie


def api_podsumowanie(s: Stan, a: Asystent, q: dict):
    oceny = _oceny_wszystkie(s, a)
    kat = Counter(o.kategoria for _, o, _, _ in oceny)
    akcje = Counter(ak for *_, ak, _ in oceny)
    chronione_kat = Counter()
    for _, o, _, _ in oceny:
        for p in o.powody_ochrony:
            chronione_kat[p.split(" (")[0].split(" "")[0]] += 1
    grupy = a._grupuj_nadawcow(oceny)
    polecane = [g for g in grupy if g["do_kwarantanny"] > 0][:8]
    firmy = Counter()
    for rec, o, _, _ in oceny:
        if o.kategoria in ("reklama", "newsletter"):
            firmy[domena_glowna(domena(rec.get("od") or ""))] += 1
    skan = {}
    return {
        "wszystkich": len(oceny), "kategorie": dict(kat),
        "do_usuniecia": akcje.get(KWARANTANNA, 0), "do_archiwum": akcje.get(ARCHIWUM, 0),
        "chronione": akcje.get(ZACHOWAJ, 0), "nadawcow": len(grupy),
        "subskrypcje": sum(1 for g in grupy if g["wypisanie"] != "brak"),
        "nieaktywne": sum(1 for g in grupy if g["nieaktywna_subskrypcja"]),
        "polecane": polecane, "firmy": [{"firma": f, "liczba": n} for f, n in firmy.most_common(10) if f],
        "powody_ochrony": chronione_kat.most_common(8),
        "skan": skan,
    }


def api_nadawcy(s: Stan, a: Asystent, q: dict):
    oceny = _oceny_wszystkie(s, a)
    grupy = a._grupuj_nadawcow(oceny)
    kat = q.get("kategoria", "")
    szukaj = q.get("szukaj", "").lower().strip()
    if kat == "chronione":
        grupy = [g for g in grupy if g["chronione"] > 0]
    elif kat == "subskrypcje":
        grupy = [g for g in grupy if g["wypisanie"] != "brak"]
        grupy.sort(key=lambda g: (not g["nieaktywna_subskrypcja"], -g["liczba"]))
    elif kat == "polecane":
        grupy = [g for g in grupy if g["do_kwarantanny"] > 0]
    elif kat:
        grupy = [g for g in grupy if g["kategoria"] == kat]
    if szukaj:
        grupy = [g for g in grupy if szukaj in g["adres"] or szukaj in (g["nazwa"] or "").lower()]
    if q.get("sortuj") == "nazwa":
        grupy.sort(key=lambda g: (g["nazwa"] or g["adres"]).lower())
    elif q.get("sortuj") == "data":
        grupy.sort(key=lambda g: g["ostatnia"], reverse=True)
    elif kat not in ("subskrypcje",):
        grupy.sort(key=lambda g: g["liczba"], reverse=True)
    reguly = a.reguly()
    zablokowani = {r["warunek"].get("nadawca") or r["warunek"].get("domena")
                   for r in reguly if r.get("nazwa", "").startswith("Blokada:")}
    for g in grupy:
        g["zablokowany"] = g["adres"] in zablokowani or g["domena"] in zablokowani
        g["firma"] = domena_glowna(g["domena"])
    return {"nadawcy": grupy[:int(q.get("limit", 300))], "razem": len(grupy)}


def api_wiadomosci(s: Stan, a: Asystent, q: dict):
    nadawca = q.get("nadawca", "").lower()
    wynik = []
    for rec, o, ak, uz in _oceny_wszystkie(s, a):
        if nadawca and rec.get("od") != nadawca:
            continue
        wynik.append({"folder": rec["_folder"], "uid": rec["uid"], "data": rec["data"][:16].replace("T", " "),
                      "temat": rec["temat"], "od": rec["od"], "nazwa": rec.get("od_nazwa", ""),
                      "kategoria": o.kategoria, "chroniona": o.chroniona,
                      "powod": "; ".join(o.powody_ochrony) if o.chroniona else uz,
                      "przeczytana": rec.get("przeczytana"), "zalaczniki": rec.get("zalaczniki", []),
                      "klucz": _klucz_grupy(rec)})
    wynik.sort(key=lambda x: x["data"], reverse=True)
    return {"wiadomosci": wynik[:int(q.get("limit", 500))], "razem": len(wynik)}


def szablon_tematu(temat: str) -> str:
    """Wsp�lny "szablon" tematu: bez liczb, cen, dat, znak�w specjalnych i prefiks�w Re:/Fwd:."""
    t = normalizuj(temat or "")
    t = re.sub(r"^\s*((re|fw|fwd|odp|pd|aw)\s*:\s*)+", "", t)
    t = re.sub(r"\d+([.,:/-]\d+)*", "#", t)
    t = re.sub(r"[^\w#% ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(t.split()[:7]) or "(bez tematu)"


def _klucz_grupy(rec) -> str:
    return domena_glowna(domena(rec.get("od") or "")) + "|" + szablon_tematu(rec.get("temat", ""))


def api_grupy(s: Stan, a: Asystent, q: dict):
    """Powtarzaj�ce si� wiadomo�ci: ta sama firma + ten sam szablon tematu."""
    minimum = int(q.get("min", 3))
    szukaj = q.get("szukaj", "").lower().strip()
    kat = q.get("kategoria", "")
    oceny_ai = a.mag._czytaj("ai_oceny.json", {})
    grupy: dict[str, dict] = {}
    for rec, o, ak, uz in _oceny_wszystkie(s, a):
        k = _klucz_grupy(rec)
        g = grupy.setdefault(k, {"klucz": k, "firma": k.split("|")[0], "szablon": k.split("|", 1)[1],
                                 "przyklad": rec.get("temat", ""), "nazwa": rec.get("od_nazwa", ""), "liczba": 0,
                                 "chronione": 0, "nieprzeczytane": 0, "ostatnia": "", "pierwsza": "9999",
                                 "nadawcy": Counter(), "kategorie": Counter()})
        g["liczba"] += 1
        g["chronione"] += 1 if o.chroniona else 0
        g["nieprzeczytane"] += 0 if rec.get("przeczytana") else 1
        if rec["data"] > g["ostatnia"]:
            g["ostatnia"], g["przyklad"], g["nazwa"] = rec["data"], rec.get("temat", ""), rec.get("od_nazwa", "")
        g["pierwsza"] = min(g["pierwsza"], rec["data"])
        g["nadawcy"][rec.get("od") or ""] += 1
        g["kategorie"][o.kategoria] += 1
    wynik = []
    for g in grupy.values():
        if g["liczba"] < minimum:
            continue
        g["kategoria"] = g["kategorie"].most_common(1)[0][0]
        if kat and g["kategoria"] != kat:
            continue
        if szukaj and szukaj not in g["firma"] and szukaj not in normalizuj(g["przyklad"]) \
                and szukaj not in (g["nazwa"] or "").lower():
            continue
        g["adresy"] = [x for x, _ in g["nadawcy"].most_common(3)]
        g["nieprzeczytane_proc"] = round(100 * g["nieprzeczytane"] / g["liczba"])
        g["do_usuniecia"] = g["liczba"] - g["chronione"]
        g["ai"] = oceny_ai.get(g["klucz"])
        g["ostatnia"], g["pierwsza"] = g["ostatnia"][:10], g["pierwsza"][:10]
        del g["nadawcy"], g["kategorie"]
        wynik.append(g)
    # pogrupuj wed�ug firm
    firmy: dict[str, dict] = {}
    for g in sorted(wynik, key=lambda x: x["liczba"], reverse=True):
        f = firmy.setdefault(g["firma"], {"firma": g["firma"], "nazwa": g["nazwa"], "liczba": 0,
                                         "do_usuniecia": 0, "grupy": []})
        f["liczba"] += g["liczba"]
        f["do_usuniecia"] += g["do_usuniecia"]
        f["grupy"].append(g)
    lista = sorted(firmy.values(), key=lambda f: f["liczba"], reverse=True)
    return {"firmy": lista[:int(q.get("limit", 150))], "grup": len(wynik),
            "wiadomosci": sum(g["liczba"] for g in wynik)}


def api_firmy(s: Stan, a: Asystent, q: dict):
    """Wszystkie adresy jednej firmy (domeny) razem."""
    szukaj = q.get("szukaj", "").lower().strip()
    firmy: dict[str, dict] = {}
    for rec, o, ak, uz in _oceny_wszystkie(s, a):
        d = domena_glowna(domena(rec.get("od") or "")) or "(brak)"
        f = firmy.setdefault(d, {"firma": d, "nazwa": rec.get("od_nazwa", ""), "liczba": 0, "chronione": 0,
                                 "polecane": 0, "nieprzeczytane": 0, "ostatnia": "", "adresy": Counter(),
                                 "kategorie": Counter()})
        f["liczba"] += 1
        f["chronione"] += 1 if o.chroniona else 0
        f["polecane"] += 1 if ak == KWARANTANNA else 0
        f["nieprzeczytane"] += 0 if rec.get("przeczytana") else 1
        if rec["data"] > f["ostatnia"]:
            f["ostatnia"], f["nazwa"] = rec["data"], rec.get("od_nazwa", "") or f["nazwa"]
        f["adresy"][rec.get("od") or ""] += 1
        f["kategorie"][o.kategoria] += 1
    wynik = []
    for f in firmy.values():
        if szukaj and szukaj not in f["firma"] and szukaj not in (f["nazwa"] or "").lower():
            continue
        f["kategoria"] = f["kategorie"].most_common(1)[0][0]
        f["adresy"] = f["adresy"].most_common(6)
        f["nieprzeczytane_proc"] = round(100 * f["nieprzeczytane"] / f["liczba"])
        f["ostatnia"] = f["ostatnia"][:10]
        del f["kategorie"]
        wynik.append(f)
    wynik.sort(key=lambda f: f["liczba"], reverse=True)
    return {"firmy": wynik[:int(q.get("limit", 300))], "razem": len(wynik)}


def api_przygotuj_usuniecie(s: Stan, a: Asystent, d: dict):
    nadawcy = [x.lower() for x in d.get("nadawcy") or []]
    wiad = d.get("wiadomosci") or []
    firmy = [x.lower() for x in d.get("firmy") or []]
    klucze = set(d.get("grupy") or [])
    polecane = bool(d.get("polecane"))
    starsze = d.get("starsze_niz_dni")
    if klucze:
        wiad = [{"folder": rec["_folder"], "uid": rec["uid"]}
                for rec, *_ in _oceny_wszystkie(s, a) if _klucz_grupy(rec) in klucze]
        if not wiad:
            raise BladAsystenta("Ta grupa jest ju� pusta - od�wie� widok.")
    if not (nadawcy or wiad or polecane or firmy):
        raise BladAsystenta("Nie wybrano nic do usuni�cia.")
    # Weto AI: przy "Usu� polecane" usuwamy tylko grupy, kt�re AI uzna�o za zb�dne;
    # przy r�cznym wyborze zatrzymujemy to, co AI uzna�o za wa�ne.
    zatrzymane = None
    if a.cfg["ai"].get("wlaczone"):
        oceny_ai = a.mag._czytaj("ai_oceny.json", {})
        wer = _wymagaj_weryfikacji(a)
        pewne_firmy = firmy_zbedne_wg_ai(oceny_ai, wer)
        if polecane:
            def zatrzymane(rec):
                k = _klucz_grupy(rec)
                st = stan_ai(oceny_ai.get(k), wer)
                if st == "niesprawdzone":
                    return k.split("|")[0] not in pewne_firmy
                return st != "usun"
        else:
            zatrzymane = lambda rec: stan_ai(oceny_ai.get(_klucz_grupy(rec)), wer) == "zachowaj"  # noqa: E731
    weto = niepewne = 0
    plany, liczba, chronione, top = [], 0, 0, Counter()
    for folder in s.foldery(a):
        uidy = [w["uid"] for w in wiad if w.get("folder") == folder] if wiad else None
        if wiad and not uidy:
            continue
        p = a.przygotuj_porzadki(folder, nadawcy=nadawcy or None, domeny=firmy or None, akcja="kosz",
                                 wedlug_rekomendacji=polecane,
                                 starsze_niz_dni=int(starsze) if starsze not in (None, "") else None,
                                 uidy=uidy, dozwolone=AKCJE_PANELU, tylko_pewne=polecane, zatrzymane=zatrzymane)
        chronione += p["pominiete_chronione"]
        weto += p["zatrzymane_przez_ai"]
        niepewne += p["pominiete_niepewne"]
        if p["liczba"]:
            plany.append(p["plan_id"])
            liczba += p["liczba"]
            top.update(dict(p["nadawcy_top"]))
    return {"plany": plany, "liczba": liczba, "chronione": chronione, "nadawcy_top": top.most_common(10),
            "zatrzymane_przez_ai": weto, "pominiete_niepewne": niepewne, "ai": bool(zatrzymane)}


def api_plan_pozycje(s: Stan, a: Asystent, q: dict):
    """Lista tego, co zostanie usuni�te - do przejrzenia i odznaczenia przed potwierdzeniem."""
    limit = int(q.get("limit", 400))
    pozycje, razem = [], 0
    for pid in [p for p in q.get("plany", "").split(",") if p]:
        plan = a.mag.plan(pid)
        if not plan:
            continue
        razem += len(plan["pozycje"])
        for p in plan["pozycje"]:
            if len(pozycje) < limit:
                pozycje.append({k: p[k] for k in ("message_id", "od", "temat", "data", "powod")})
    pozycje.sort(key=lambda p: p["data"], reverse=True)
    return {"pozycje": pozycje, "razem": razem}


def api_wykonaj(s: Stan, a: Asystent, d: dict):
    razem, pominiete = 0, 0
    pomin = d.get("pomin") or []
    for pid in d.get("plany") or []:
        w = a.wykonaj_plan(pid, pomin=pomin)
        razem += sum(w["przeniesiono"].values())
        pominiete += w["pominiete"]
    return {"przeniesiono": razem, "pominiete": pominiete, "plany": d.get("plany")}


def _ostatni_z_wypisaniem(s: Stan, a: Asystent, adres: str):
    recs = [rec for rec, *_ in _oceny_wszystkie(s, a) if rec.get("od") == adres]
    recs.sort(key=lambda r: r["data"], reverse=True)
    return next((r for r in recs if r.get("n", {}).get("list-unsubscribe")), None), len(recs)


def api_wypisz(s: Stan, a: Asystent, d: dict):
    adres = (d.get("adres") or "").lower()
    rec, liczba = _ostatni_z_wypisaniem(s, a, adres)
    if not rec:
        return {"ok": False, "info": "Ten nadawca nie podaje sposobu wypisania. Mo�esz go zablokowa�."}
    m = wypisywanie.metody(rec["n"]["list-unsubscribe"], rec["n"].get("list-unsubscribe-post", ""))
    if s.fabryka:  # tryb demonstracyjny - nic nie jest wysy�ane
        _blokuj(a, adres, False)
        return {"ok": True, "info": f"(demo) Wypisano z {adres} - nowe wiadomo�ci trafi� do Kosza.", "liczba": liczba}
    if m["rodzaj"] == "link-reczny":
        a.mag.dziennik("wypisanie_link_reczny", adres=adres, url=m["https"][0])
        return {"ok": False, "link": m["https"][0],
                "info": "Ta firma wymaga potwierdzenia na swojej stronie. Otw�rz link i potwierd� wypisanie."}
    ok, info = a.wykonaj_wypisanie({"adres": adres, "metoda": m, "zablokuj_po": False}, haslo=s.haslo)
    if ok:
        _blokuj(a, adres, False)
    return {"ok": ok, "info": info, "liczba": liczba}


def _blokuj(a: Asystent, adres: str, cala_domena: bool):
    if cala_domena:
        d = domena_glowna(domena(adres)) if "@" in adres else adres
        return a.dodaj_regule(f"Blokada: {d}", akcja="kosz", domena_=d, zatwierdzona=True, dozwolone=AKCJE_PANELU)
    return a.dodaj_regule(f"Blokada: {adres}", akcja="kosz", nadawca=adres, zatwierdzona=True,
                          dozwolone=AKCJE_PANELU)


def api_blokuj(s, a, d):
    adres = (d.get("adres") or "").lower().strip()
    if not adres:
        raise BladAsystenta("Brak adresu.")
    return _blokuj(a, adres, bool(d.get("cala_domena")))


def api_odblokuj(s, a, d):
    nazwy = {f"Blokada: {(d.get('adres') or '').lower()}",
             f"Blokada: {domena_glowna(domena(d.get('adres') or ''))}"}
    for n in nazwy:
        a.usun_regule(n)
    return {"ok": True}


def api_chron(s, a, d):
    return a.chron(d.get("adres") or "")


def api_odchron(s, a, d):
    adres = (d.get("adres") or "").lower().strip()
    l = a.mag.listy()
    l["chronieni"] = [x for x in l["chronieni"] if x != adres]
    a.mag.zapisz_listy(l)
    a.mag.dziennik("zdjeto_ochrone", adres=adres, przez="panel")
    return l


def api_reguly(s, a, q):
    return {"reguly": a.reguly(), "auto": a.cfg["panel"].get("auto_czyszczenie", False),
            "auto_co_minut": a.cfg["panel"].get("auto_co_minut", 60), "kategorie": KATEGORIE}


def api_dodaj_regule(s, a, d):
    akcja = d.get("akcja") or "kosz"
    return a.dodaj_regule(d.get("nazwa") or "Regu�a", akcja=akcja, nadawca=d.get("nadawca") or None,
                          domena_=d.get("domena") or None, kategoria=d.get("kategoria") or None,
                          temat_zawiera=d.get("temat_zawiera") or None,
                          starsze_niz_dni=int(d["starsze_niz_dni"]) if d.get("starsze_niz_dni") else None,
                          zatwierdzona=True, dozwolone=AKCJE_PANELU)


def api_usun_regule(s, a, d):
    return a.usun_regule(d.get("nazwa") or "")


def api_zatwierdz_regule(s, a, d):
    reguly = a.reguly()
    for r in reguly:
        if r.get("nazwa") == d.get("nazwa"):
            r["zatwierdzona"] = True
    a.mag.zapisz_reguly(reguly)
    a.mag.dziennik("regula_zatwierdzona", nazwa=d.get("nazwa"), przez="panel")
    return {"reguly": reguly}


def api_podglad_regul(s, a, d):
    plany, liczba, top, chronione = [], 0, Counter(), 0
    for folder in s.foldery(a):
        p = a.zastosuj_reguly(folder, tylko_zatwierdzone=True)
        chronione += p.get("pominiete_chronione", 0)
        if p.get("plan_id"):
            plany.append(p["plan_id"])
            liczba += p["liczba"]
            top.update(dict(p["nadawcy_top"]))
    return {"plany": plany, "liczba": liczba, "chronione": chronione, "nadawcy_top": top.most_common(10)}


def api_ustawienia(s, a, d):
    cfg = konfiguracja.wczytaj(s.katalog)
    if "auto_czyszczenie" in d:
        cfg["panel"]["auto_czyszczenie"] = bool(d["auto_czyszczenie"])
    if d.get("auto_co_minut"):
        cfg["panel"]["auto_co_minut"] = max(15, int(d["auto_co_minut"]))
    konfiguracja.zapisz(cfg, s.katalog)
    a.mag.dziennik("ustawienia", panel=cfg["panel"])
    return cfg["panel"]


def api_usuniete(s, a, q):
    wpisy = [w for w in a.mag.kwarantanna() if w.get("status") in ("w_koszu", "w_kwarantannie", "zarchiwizowana")]
    wpisy.sort(key=lambda w: w.get("przeniesiono", ""), reverse=True)
    return {"wpisy": wpisy[:1000], "razem": len(wpisy)}


def api_przywroc(s, a, d):
    return a.przywroc(plan_id=d.get("plan_id"), nadawca=d.get("nadawca"), message_ids=d.get("message_ids"))


def api_kosz_oproznij(s, a, d):
    if (d.get("potwierdzenie") or "").strip().upper() != "USUN TRWALE":
        raise BladAsystenta("Dla trwa�ego usuni�cia wpisz dok�adnie: USUN TRWALE")
    return a.oproznij_kosz(message_ids=d.get("message_ids"))


def api_historia(s, a, q):
    return {"wpisy": list(reversed(a.dziennik(150)))}


def api_podglad(s, a, q):
    return a.podglad(q.get("folder", "INBOX"), int(q.get("uid", 0)))


def api_ai_stan(s, a, q):
    cfg = a.cfg["ai"]
    oceny = a.mag._czytaj("ai_oceny.json", {})
    licz = Counter(o.get("decyzja") for o in oceny.values())
    try:
        zapisany = bool(ai_mod.klucz())
    except Exception:
        zapisany = False
    return {**cfg, "klucz_zapisany": zapisany, "ocenionych_grup": len(oceny), "usun": licz.get("usun", 0),
            "zachowaj": licz.get("zachowaj", 0), "niepewne": licz.get("niepewne", 0), "zadanie": s.ai_zadanie}


def api_ai_zapisz(s, a, d):
    cfg = konfiguracja.wczytaj(s.katalog)
    for k in ("adres_api", "model"):
        if k in d:
            cfg["ai"][k] = (d[k] or "").strip()
    if "wlaczone" in d:
        cfg["ai"]["wlaczone"] = bool(d["wlaczone"])
    if d.get("maks_grup"):
        cfg["ai"]["maks_grup"] = max(10, min(1000, int(d["maks_grup"])))
    if d.get("klucz"):
        ai_mod.ustaw_klucz(d["klucz"])
    if d.get("usun_klucz"):
        ai_mod.usun_klucz()
    if cfg["ai"]["wlaczone"] and not (cfg["ai"]["adres_api"] and cfg["ai"]["model"]):
        raise BladAsystenta("Aby w��czy� AI, podaj adres API i nazw� modelu.")
    konfiguracja.zapisz(cfg, s.katalog)
    a.mag.dziennik("ustawienia_ai", wlaczone=cfg["ai"]["wlaczone"], adres=cfg["ai"]["adres_api"],
                   model=cfg["ai"]["model"])
    return api_ai_stan(s, Asystent(s.katalog), {})


def _klient_ai(a: Asystent) -> ai_mod.KlientAI:
    cfg = a.cfg["ai"]
    return ai_mod.KlientAI(cfg.get("adres_api", ""), cfg.get("model", ""), ai_mod.klucz() or "")


def api_ai_test(s, a, d):
    k = _klient_ai(a)
    try:
        modele = k.modele()
    except ai_mod.BladAI as e:
        modele, blad_modeli = [], str(e)
    else:
        blad_modeli = None
    odp = k.czat("Odpowiadasz wy��cznie JSON.", 'Zwr�� dok�adnie: {"ok": true}')
    return {"ok": True, "odpowiedz": odp[:200], "modele": modele[:40], "blad_listy_modeli": blad_modeli}


MIN_GRUP_FIRMY = 3


def stan_ai(o: dict | None, wymagaj_weryfikacji: bool) -> str:
    """usun / zachowaj / niepewne / czeka_na_weryfikacje / niesprawdzone"""
    if not o:
        return "niesprawdzone"
    if o.get("model") == "u�ytkownik":
        return "usun" if o.get("decyzja") == "usun" else "zachowaj"
    if o.get("decyzja") != "usun":
        return o.get("decyzja") or "niepewne"
    if wymagaj_weryfikacji:
        w = o.get("weryfikacja")
        if not w:
            return "czeka_na_weryfikacje"
        if w.get("decyzja") != "usun":
            return "zachowaj" if w.get("decyzja") == "zachowaj" else "niepewne"
    return "usun"


def _wymagaj_weryfikacji(a: Asystent) -> bool:
    return bool(a.cfg["ai"].get("model_weryfikacji"))


def firmy_zbedne_wg_ai(oceny_ai: dict, wymagaj_weryfikacji: bool = False) -> set[str]:
    """Firmy, dla kt�rych AI potwierdzi�o co najmniej 3 grupy jako zb�dne i �ADNEJ nie uzna�o
    za wa�n�/niepewn� (ani nie czeka ona na weryfikacj�)."""
    st: dict[str, Counter] = {}
    for k, v in oceny_ai.items():
        st.setdefault(k.split("|")[0], Counter())[stan_ai(v, wymagaj_weryfikacji)] += 1
    return {f for f, c in st.items() if c.get("usun", 0) >= MIN_GRUP_FIRMY and set(c) == {"usun"}}


def _grupy_kandydatow(s: Stan, a: Asystent) -> dict[str, dict]:
    """Wszystkie grupy wiadomo�ci, kt�re lokalne regu�y z WYSOK� pewno�ci� uznaj� za zb�dne."""
    grupy: dict[str, dict] = {}
    for rec, o, ak, uz in _oceny_wszystkie(s, a):
        if o.chroniona or ak != KWARANTANNA or o.pewnosc != "wysoka":
            continue
        k = _klucz_grupy(rec)
        g = grupy.setdefault(k, {"klucz": k, "firma": k.split("|")[0], "liczba": 0, "tematy": [],
                                 "nadawcy": Counter(), "nazwa": "", "kategorie": Counter(), "rep": None,
                                 "data": "", "pierwsza": "9999", "otwarte": 0, "wiadomosci": [],
                                 "wypisanie": {}})
        g["liczba"] += 1
        g["nadawcy"][rec.get("od") or ""] += 1
        g["kategorie"][o.kategoria] += 1
        g["otwarte"] += 1 if rec.get("przeczytana") else 0
        g["pierwsza"] = min(g["pierwsza"], rec["data"])
        g["wiadomosci"].append((rec["_folder"], rec["uid"]))
        lu = rec.get("n", {}).get("list-unsubscribe")
        if lu and (rec.get("od") or "") not in g["wypisanie"]:
            g["wypisanie"][rec.get("od") or ""] = wypisywanie.metody(lu, rec["n"].get("list-unsubscribe-post", ""))["rodzaj"]
        if len(g["tematy"]) < 3 and rec.get("temat") not in g["tematy"]:
            g["tematy"].append(rec.get("temat", ""))
        if rec["data"] > g["data"]:
            g["data"], g["rep"], g["nazwa"] = rec["data"], (rec["_folder"], rec["uid"]), rec.get("od_nazwa", "")
            g["zalaczniki"] = rec.get("zalaczniki_nazwy", [])
    for g in grupy.values():
        g["nadawca"] = g["nadawcy"].most_common(1)[0][0]
        g["kategoria"] = g["kategorie"].most_common(1)[0][0]
    return grupy


def _po_firmach(lista: list[dict]) -> list[dict]:
    """Najpierw po kilka najwi�kszych grup z ka�dej firmy (�eby szybko "rozstrzyga�" ca�e firmy)."""
    lista = sorted(lista, key=lambda g: g["liczba"], reverse=True)
    na_firme: Counter = Counter()
    pierwsze, reszta = [], []
    for g in lista:
        if na_firme[g["firma"]] < MIN_GRUP_FIRMY:
            na_firme[g["firma"]] += 1
            pierwsze.append(g)
        else:
            reszta.append(g)
    return pierwsze + reszta


def _kandydaci_ai(s: Stan, a: Asystent, ponownie: bool) -> list[dict]:
    """Grupy do 1. sprawdzenia przez AI."""
    oceny_ai = {} if ponownie else a.mag._czytaj("ai_oceny.json", {})
    pewne_firmy = firmy_zbedne_wg_ai(oceny_ai, _wymagaj_weryfikacji(a))
    return _po_firmach([g for k, g in _grupy_kandydatow(s, a).items()
                        if k not in oceny_ai and g["firma"] not in pewne_firmy])


def _kandydaci_weryfikacji(s: Stan, a: Asystent) -> list[dict]:
    """Grupy, kt�re 1. model chce usun��, a 2. (sceptyczny) jeszcze nie sprawdzi�."""
    oceny_ai = a.mag._czytaj("ai_oceny.json", {})
    return _po_firmach([g for k, g in _grupy_kandydatow(s, a).items()
                        if stan_ai(oceny_ai.get(k), True) == "czeka_na_weryfikacje"])


def api_ai_sprawdz(s, a, d):
    if s.ai_zadanie["w_toku"]:
        return {"uruchomiono": False, "info": "Sprawdzanie ju� trwa."}
    if not a.cfg["ai"].get("wlaczone"):
        raise BladAsystenta("Sprawdzanie przez AI jest wy��czone - w��cz je w Ustawieniach.")
    klient = _klient_ai(a)
    model2 = a.cfg["ai"].get("model_weryfikacji")
    klient2 = ai_mod.KlientAI(a.cfg["ai"]["adres_api"], model2, ai_mod.klucz() or "") if model2 else None
    maks = int(a.cfg["ai"].get("maks_grup", 150))
    etap1 = _kandydaci_ai(s, a, bool(d.get("ponownie")))[:maks]
    s.ai_zadanie = {"w_toku": True, "etap": 1, "zrobione": 0, "razem": len(etap1), "blad": None, "wynik": None}

    def uzupelnij(grupy):
        fr = a.fragmenty([tuple(g["rep"]) for g in grupy if g["rep"]])
        for g in grupy:
            f = fr.get(tuple(g["rep"]) if g["rep"] else None) or {}
            g["fragment"] = f.get("fragment", "")
            g["zalaczniki"] = (g.get("zalaczniki") or []) + f.get("zalaczniki", [])

    def przebieg(grupy, kl, system, pole):
        licz = Counter()
        for i in range(0, len(grupy), ai_mod.PARTIA):
            czesc = grupy[i:i + ai_mod.PARTIA]
            wynik = ai_mod.ocen_grupy(kl, czesc, system)
            zapis = a.mag._czytaj("ai_oceny.json", {})
            for k, v in wynik.items():
                wpis = {**v, "czas": time.strftime("%Y-%m-%d %H:%M"), "model": kl.model}
                if pole:
                    zapis.setdefault(k, {})[pole] = wpis
                else:
                    zapis[k] = wpis
                licz[v["decyzja"]] += 1
            a.mag._zapisz("ai_oceny.json", zapis)
            s.ai_zadanie["zrobione"] = min(len(grupy), i + len(czesc))
        return licz

    def praca():
        try:
            wynik = {}
            if etap1:
                uzupelnij(etap1)
                wynik["etap1"] = dict(przebieg(etap1, klient, ai_mod.SYSTEM, None))
            if klient2:
                etap2 = _kandydaci_weryfikacji(s, a)[:maks]
                s.ai_zadanie.update(etap=2, zrobione=0, razem=len(etap2))
                if etap2:
                    uzupelnij(etap2)
                    wynik["etap2"] = dict(przebieg(etap2, klient2, ai_mod.SYSTEM_WERYFIKACJA, "weryfikacja"))
            s.ai_zadanie["wynik"] = wynik
            s.ai_zadanie["pozostalo"] = len(_kandydaci_ai(s, a, False)) + \
                (len(_kandydaci_weryfikacji(s, a)) if klient2 else 0)
            a.mag.dziennik("ai_sprawdzenie", wynik=wynik, model=klient.model, model_weryfikacji=model2)
        except Exception as e:  # noqa: BLE001
            s.ai_zadanie["blad"] = str(e)
            traceback.print_exc()
        finally:
            s.ai_zadanie["w_toku"] = False
    threading.Thread(target=praca, daemon=True).start()
    return {"uruchomiono": True, "grup": len(etap1)}


NAZWY_SEKCJI = {"reklama": "Reklamy", "newsletter": "Newslettery", "powiadomienie": "Powiadomienia",
                "spam": "Spam", "spo�eczno�ciowe": "Serwisy spo�eczno�ciowe", "transakcyjne": "Sklepy i zam�wienia",
                "osobiste": "Inne", "inne": "Inne"}


def _co_ile_dni(pierwsza: str, ostatnia: str, n: int) -> float | None:
    from datetime import datetime
    try:
        dni = (datetime.fromisoformat(ostatnia) - datetime.fromisoformat(pierwsza)).total_seconds() / 86400
    except Exception:
        return None
    return round(dni / (n - 1), 1) if n > 1 else None


def _decyzje_sprzatania(s: Stan, a: Asystent):
    """Dla ka�dej grupy-kandydata: czy idzie do usuni�cia i dlaczego."""
    grupy = _grupy_kandydatow(s, a)
    ai_wl = bool(a.cfg["ai"].get("wlaczone"))
    oceny_ai = a.mag._czytaj("ai_oceny.json", {}) if ai_wl else {}
    wer = _wymagaj_weryfikacji(a)
    pewne_firmy = firmy_zbedne_wg_ai(oceny_ai, wer) if ai_wl else set()
    for k, g in grupy.items():
        o = oceny_ai.get(k)
        st = stan_ai(o, wer) if ai_wl else "usun"
        g["ai_stan"] = st
        if ai_wl and st == "niesprawdzone" and g["firma"] in pewne_firmy:
            g["ai_stan"] = "usun"
            g["ai_opis"] = "AI potwierdzi�o inne wiadomo�ci tej firmy jako zb�dne"
        elif o:
            g["ai_opis"] = o.get("powod", "")
            if o.get("weryfikacja"):
                g["ai_opis2"] = o["weryfikacja"].get("powod", "")
        g["do_usuniecia"] = g["ai_stan"] == "usun"
    return grupy, ai_wl


def api_sprzatanie(s: Stan, a: Asystent, q: dict):
    """Podgl�d sprz�tania: co zostanie usuni�te (posegregowane) i co zostaje - z powodami."""
    grupy, ai_wl = _decyzje_sprzatania(s, a)
    sekcje: dict[str, dict] = {}
    zatrzymane = []
    for g in grupy.values():
        if not g["do_usuniecia"]:
            zatrzymane.append(g)
            continue
        sek = sekcje.setdefault(g["kategoria"], {"kategoria": g["kategoria"],
                                                 "nazwa": NAZWY_SEKCJI.get(g["kategoria"], g["kategoria"]),
                                                 "liczba": 0, "firmy": {}})
        f = sek["firmy"].setdefault(g["firma"], {"firma": g["firma"], "nazwa": g["nazwa"], "liczba": 0, "otwarte": 0,
                                                "pierwsza": "9999", "ostatnia": "", "grupy": [], "wypisanie": {},
                                                "adresy": Counter()})
        sek["liczba"] += g["liczba"]
        f["liczba"] += g["liczba"]
        f["otwarte"] += g["otwarte"]
        f["pierwsza"] = min(f["pierwsza"], g["pierwsza"])
        f["ostatnia"] = max(f["ostatnia"], g["data"])
        f["adresy"].update(g["nadawcy"])
        for adr, met in g["wypisanie"].items():
            f["wypisanie"].setdefault(adr, met)
        f["grupy"].append(g)
    wynik_sekcje = []
    for sek in sorted(sekcje.values(), key=lambda x: x["liczba"], reverse=True):
        firmy = []
        for f in sorted(sek["firmy"].values(), key=lambda x: x["liczba"], reverse=True):
            f["grupy"].sort(key=lambda g: g["liczba"], reverse=True)
            wyp = []
            for adr, met in f["wypisanie"].items():
                n = f["adresy"].get(adr, 0)
                wyp.append({"adres": adr, "metoda": met, "liczba": n,
                            "sugerowane": met in ("one-click", "mailto") and n >= 5 and f["otwarte"] / f["liczba"] < 0.15})
            firmy.append({
                "firma": f["firma"], "nazwa": f["nazwa"], "liczba": f["liczba"],
                "otwarte_proc": round(100 * f["otwarte"] / f["liczba"]), "pierwsza": f["pierwsza"][:10],
                "ostatnia": f["ostatnia"][:10], "co_ile_dni": _co_ile_dni(f["pierwsza"], f["ostatnia"], f["liczba"]),
                "adresy": [x for x, _ in f["adresy"].most_common(4)], "wypisanie": wyp,
                "grup": len(f["grupy"]),
                "grupy": [{"klucz": g["klucz"], "przyklad": g["tematy"][0] if g["tematy"] else "", "liczba": g["liczba"],
                           "pierwsza": g["pierwsza"][:10], "ostatnia": g["data"][:10],
                           "otwarte_proc": round(100 * g["otwarte"] / g["liczba"]), "nadawca": g["nadawca"],
                           "ai_stan": g["ai_stan"], "ai_opis": g.get("ai_opis", ""), "ai_opis2": g.get("ai_opis2", "")}
                          for g in f["grupy"][:25]],
            })
        wynik_sekcje.append({"kategoria": sek["kategoria"], "nazwa": sek["nazwa"], "liczba": sek["liczba"],
                             "firmy": firmy})
    # co zostaje
    st_licz = Counter(g["ai_stan"] for g in zatrzymane)
    st_wiad = Counter()
    for g in zatrzymane:
        st_wiad[g["ai_stan"]] += g["liczba"]
    oceny = _oceny_wszystkie(s, a)
    chronione = Counter()
    chronione_razem = 0
    for _, o, _, _ in oceny:
        if o.chroniona:
            chronione_razem += 1
            chronione[o.powody_ochrony[0].split(" (")[0].split(" "")[0]] += 1
    zatrzymane.sort(key=lambda g: (g["ai_stan"] != "zachowaj", -g["liczba"]))
    return {
        "ai": ai_wl, "model": a.cfg["ai"].get("model"), "model_weryfikacji": a.cfg["ai"].get("model_weryfikacji"),
        "wszystkich": len(oceny), "do_usuniecia": sum(x["liczba"] for x in wynik_sekcje),
        "sekcje": wynik_sekcje,
        "zostaje": {
            "chronione": chronione_razem, "chronione_powody": chronione.most_common(8),
            "ai_wazne": st_wiad.get("zachowaj", 0), "ai_niepewne": st_wiad.get("niepewne", 0),
            "niesprawdzone": st_wiad.get("niesprawdzone", 0) + st_wiad.get("czeka_na_weryfikacje", 0),
            "grupy_niesprawdzone": st_licz.get("niesprawdzone", 0) + st_licz.get("czeka_na_weryfikacje", 0),
            "przyklady": [{"klucz": g["klucz"], "firma": g["firma"], "przyklad": g["tematy"][0] if g["tematy"] else "",
                           "liczba": g["liczba"], "ai_stan": g["ai_stan"], "ai_opis": g.get("ai_opis", ""),
                           "ai_opis2": g.get("ai_opis2", ""), "nadawca": g["nadawca"]}
                          for g in zatrzymane if g["ai_stan"] in ("zachowaj", "niepewne")][:60],
        },
    }


def api_sprzatanie_wykonaj(s: Stan, a: Asystent, d: dict):
    """Wykonuje sprz�tanie zatwierdzone w podgl�dzie: przenosi do Kosza i (opcjonalnie) wypisuje."""
    bez_firm = set(d.get("bez_firm") or [])
    bez_grup = set(d.get("bez_grup") or [])
    wypisz = [x.lower() for x in d.get("wypisz") or []]
    grupy, _ = _decyzje_sprzatania(s, a)
    klucze = {k for k, g in grupy.items() if g["do_usuniecia"] and g["firma"] not in bez_firm and k not in bez_grup}
    po_folderach: dict[str, list[int]] = {}
    for k in klucze:
        for folder, uid in grupy[k]["wiadomosci"]:
            po_folderach.setdefault(folder, []).append(uid)
    plany, przeniesiono, pominiete = [], 0, 0
    for folder, uidy in po_folderach.items():
        p = a.przygotuj_porzadki(folder, akcja="kosz", wedlug_rekomendacji=True, uidy=uidy,
                                 dozwolone=AKCJE_PANELU, tylko_pewne=True)
        if p["liczba"]:
            w = a.wykonaj_plan(p["plan_id"])
            plany.append(p["plan_id"])
            przeniesiono += sum(w["przeniesiono"].values())
            pominiete += w["pominiete"]
    wypisane, linki, bledy = [], [], []
    for adr in wypisz:
        try:
            r = api_wypisz(s, a, {"adres": adr})
        except Exception as e:  # noqa: BLE001
            bledy.append({"adres": adr, "info": str(e)})
            continue
        if r.get("link"):
            linki.append({"adres": adr, "link": r["link"]})
        elif r.get("ok"):
            wypisane.append(adr)
        else:
            bledy.append({"adres": adr, "info": r.get("info", "")})
    a.mag.dziennik("sprzatanie", przeniesiono=przeniesiono, plany=plany, wypisane=wypisane,
                   linki=len(linki), bledy=len(bledy))
    return {"przeniesiono": przeniesiono, "pominiete": pominiete, "plany": plany, "wypisane": wypisane,
            "linki": linki, "bledy": bledy}


def api_przywroc_plany(s, a, d):
    razem = {"przywrocone": 0, "nieznalezione": 0}
    for pid in d.get("plany") or []:
        r = a.przywroc(plan_id=pid)
        razem["przywrocone"] += r["przywrocone"]
        razem["nieznalezione"] += r["nieznalezione"]
    return razem


def api_ai_zmien(s, a, d):
    """U�ytkownik r�cznie poprawia decyzj� AI dla grupy."""
    zapis = a.mag._czytaj("ai_oceny.json", {})
    k = d.get("klucz") or ""
    if d.get("decyzja") not in ("usun", "zachowaj", "niepewne"):
        raise BladAsystenta("Niepoprawna decyzja.")
    zapis[k] = {"decyzja": d["decyzja"], "powod": "Twoja decyzja", "czas": time.strftime("%Y-%m-%d %H:%M"),
                "model": "u�ytkownik"}
    a.mag._zapisz("ai_oceny.json", zapis)
    return zapis[k]


GET = {"/api/podsumowanie": api_podsumowanie, "/api/ai": api_ai_stan, "/api/sprzatanie": api_sprzatanie, "/api/plan_pozycje": api_plan_pozycje, "/api/nadawcy": api_nadawcy, "/api/wiadomosci": api_wiadomosci,
       "/api/grupy": api_grupy, "/api/firmy": api_firmy,
       "/api/reguly": api_reguly, "/api/usuniete": api_usuniete, "/api/historia": api_historia,
       "/api/podglad": api_podglad}
POST = {"/api/przygotuj_usuniecie": api_przygotuj_usuniecie, "/api/wykonaj": api_wykonaj,
        "/api/wypisz": api_wypisz, "/api/blokuj": api_blokuj, "/api/odblokuj": api_odblokuj,
        "/api/chron": api_chron, "/api/odchron": api_odchron, "/api/reguly": api_dodaj_regule,
        "/api/reguly/usun": api_usun_regule, "/api/reguly/zatwierdz": api_zatwierdz_regule,
        "/api/reguly/podglad": api_podglad_regul, "/api/ustawienia": api_ustawienia,
        "/api/przywroc": api_przywroc, "/api/ai": api_ai_zapisz, "/api/ai/test": api_ai_test,
        "/api/ai/sprawdz": api_ai_sprawdz, "/api/ai/zmien": api_ai_zmien,
        "/api/sprzatanie/wykonaj": api_sprzatanie_wykonaj, "/api/przywroc_plany": api_przywroc_plany,
        "/api/kosz/oproznij": api_kosz_oproznij}


# ---------------------------------------------------------------------- HTTP
def zrob_handler(s: Stan, port: int):
    dozwolone_hosty = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _wyslij(self, kod: int, cialo: bytes, typ="application/json; charset=utf-8", naglowki=None):
            self.send_response(kod)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(cialo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: https://icons.duckduckgo.com; "
                             "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            for k, v in (naglowki or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(cialo)

        def _json(self, dane, kod=200, naglowki=None):
            self._wyslij(kod, json.dumps(dane, ensure_ascii=False, default=str).encode("utf-8"), naglowki=naglowki)

        def _host_ok(self):
            return self.headers.get("Host", "") in dozwolone_hosty

        def _zalogowany(self):
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return bool(s.token) and "sesja" in c and secrets.compare_digest(c["sesja"].value, s.token)

        def _cialo(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise BladAsystenta("Za du�e ��danie.")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            if not self._host_ok():
                return self._wyslij(403, b"Forbidden", "text/plain")
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if u.path == "/api/stan":
                cfg = konfiguracja.wczytaj(s.katalog)
                email = cfg["konto"]["email"]
                zapisane = bool(email and (s.fabryka or konfiguracja.haslo(email)))
                return self._json({"zalogowany": self._zalogowany(), "email": email, "haslo_zapisane": zapisane,
                                   "skan": s.skan, "ai": s.ai_zadanie if self._zalogowany() else None})
            if u.path.startswith("/api/"):
                if not self._zalogowany():
                    return self._json({"blad": "Zaloguj si�."}, 401)
                f = GET.get(u.path)
                if not f:
                    return self._json({"blad": "Nie ma takiej funkcji."}, 404)
                return self._wykonaj(f, q)
            # pliki statyczne
            nazwa = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            plik = (WWW / nazwa).resolve()
            if WWW not in plik.parents or not plik.is_file():
                return self._wyslij(404, b"Not found", "text/plain")
            typ = mimetypes.guess_type(plik.name)[0] or "application/octet-stream"
            if typ.startswith("text/") or typ.endswith("javascript"):
                typ += "; charset=utf-8"
            self._wyslij(200, plik.read_bytes(), typ)

        def do_POST(self):
            if not self._host_ok() or "application/json" not in self.headers.get("Content-Type", ""):
                return self._wyslij(403, b"Forbidden", "text/plain")
            u = urllib.parse.urlparse(self.path)
            try:
                d = self._cialo()
            except Exception:
                return self._json({"blad": "Niepoprawne dane."}, 400)
            if u.path == "/api/zaloguj":
                return self._zaloguj(d)
            if not self._zalogowany():
                return self._json({"blad": "Zaloguj si�."}, 401)
            if u.path == "/api/wyloguj":
                s.token = None
                s.haslo = None
                if d.get("zapomnij_haslo"):
                    cfg = konfiguracja.wczytaj(s.katalog)
                    try:
                        import keyring
                        keyring.delete_password(konfiguracja.USLUGA_KEYRING, cfg["konto"]["email"])
                    except Exception:
                        pass
                return self._json({"ok": True})
            if u.path == "/api/skanuj":
                return self._json({"uruchomiono": s.uruchom_skan(po_skanie_auto=True)})
            if u.path == "/api/zamknij":
                self._json({"ok": True})
                threading.Thread(target=s.serwer.shutdown, daemon=True).start()
                return
            f = POST.get(u.path)
            if not f:
                return self._json({"blad": "Nie ma takiej funkcji."}, 404)
            return self._wykonaj(f, d)

        def _wykonaj(self, f, arg):
            try:
                a = s.asystent()
                return self._json(f(s, a, arg))
            except (BladAsystenta, BladImap) as e:
                return self._json({"blad": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._json({"blad": f"Nieoczekiwany b��d: {e}"}, 500)

        def _zaloguj(self, d):
            cfg = konfiguracja.wczytaj(s.katalog)
            email = (d.get("email") or cfg["konto"]["email"]).strip().lower()
            haslo = d.get("haslo") or ""
            if not haslo and not s.fabryka:
                haslo = konfiguracja.haslo(email) or ""
            if "@" not in email or (not haslo and not s.fabryka):
                return self._json({"blad": "Podaj adres e-mail i has�o."}, 400)
            if not s.fabryka:
                try:
                    with Klient(cfg["konto"]["imap_host"], int(cfg["konto"]["imap_port"]), email, haslo):
                        pass
                except BladImap as e:
                    return self._json({"blad": str(e)}, 400)
                if email != cfg["konto"]["email"]:
                    cfg["konto"]["email"] = email
                    konfiguracja.zapisz(cfg, s.katalog)
                if d.get("zapamietaj", True):
                    konfiguracja.ustaw_haslo(email, haslo)
                s.haslo = haslo
            s.token = secrets.token_urlsafe(32)
            Asystent(s.katalog).mag.dziennik("logowanie", email=email)
            s.uruchom_skan(po_skanie_auto=True)
            return self._json({"ok": True}, naglowki={
                "Set-Cookie": f"sesja={s.token}; HttpOnly; SameSite=Strict; Path=/"})

    return H


def _juz_dziala(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stan", timeout=1.5) as r:
            return r.status == 200 and b"zalogowany" in r.read()
    except Exception:
        return False


def uruchom(port: int | None = None, otworz=True, katalog=None, fabryka=None):
    import sys
    cfg = konfiguracja.wczytaj(katalog)
    port = port or int(cfg["panel"].get("port", 8765))
    if sys.stdout is None:  # uruchomione przez pythonw (bez okna konsoli)
        log = open((Path(katalog) if katalog else konfiguracja.katalog_projektu()) / "dane" / "panel.log",
                   "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    if _juz_dziala(port):
        if otworz:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return None
    s = Stan(katalog, fabryka)
    for p in range(port, port + 10):
        try:
            serwer = ThreadingHTTPServer(("127.0.0.1", p), zrob_handler(s, p))
            break
        except OSError:
            continue
    else:
        raise SystemExit("Nie znaleziono wolnego portu.")
    s.serwer = serwer
    adres = f"http://127.0.0.1:{p}/"
    print(f"Panel dzia�a: {adres}  (zamknij przyciskiem w panelu lub Ctrl+C)")
    threading.Thread(target=s.petla_auto, daemon=True).start()
    if otworz:
        threading.Timer(0.8, lambda: webbrowser.open(adres)).start()
    try:
        serwer.serve_forever()
    except KeyboardInterrupt:
        pass
    return s
