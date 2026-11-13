"""Klasyfikacja wiadomo�ci i twarde regu�y ochrony.

Wszystko dzia�a wy��cznie na nag��wkach (bez czytania tre�ci). Ochrona ma
zawsze pierwsze�stwo przed klasyfikacj�: wiadomo�� chroniona nigdy nie jest
proponowana do przeniesienia ani usuni�cia.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --- Kategorie -------------------------------------------------------------

SPAM = "spam"
REKLAMA = "reklama"
NEWSLETTER = "newsletter"
POWIADOMIENIE = "powiadomienie"
SPOLECZNOSCIOWE = "spo�eczno�ciowe"
TRANSAKCYJNE = "transakcyjne"
OSOBISTE = "osobiste"
INNE = "inne"

KATEGORIE = [SPAM, REKLAMA, NEWSLETTER, POWIADOMIENIE, SPOLECZNOSCIOWE, TRANSAKCYJNE, OSOBISTE, INNE]

# --- Listy domen -----------------------------------------------------------

DOMENY_URZEDOWE = [
    "gov.pl", "zus.pl", "nfz.gov.pl", "pfron.org.pl", "europa.eu", "e-doreczenia.gov.pl",
    "epuap.gov.pl", "podatki.gov.pl", "mf.gov.pl", "biznes.gov.pl", "ceidg.gov.pl",
    "ms.gov.pl", "sad.gov.pl", "krs.gov.pl", "cepik.gov.pl", "uodo.gov.pl", "uokik.gov.pl",
    "knf.gov.pl", "policja.gov.pl", "kas.gov.pl", "stat.gov.pl", "obywatel.gov.pl",
    "notariusze.pl", "krn.org.pl", "komornik.pl", "kik.pl",
]

DOMENY_FINANSOWE = [
    "pkobp.pl", "ipko.pl", "inteligo.pl", "mbank.pl", "ing.pl", "ingbank.pl", "pekao.com.pl",
    "pekao24.pl", "santander.pl", "bankmillennium.pl", "aliorbank.pl", "credit-agricole.pl",
    "bnpparibas.pl", "citihandlowy.pl", "velobank.pl", "nestbank.pl", "bankpocztowy.pl",
    "bosbank.pl", "bps.pl", "bgk.pl", "toyotabank.pl", "plusbank.pl", "revolut.com",
    "wise.com", "paypal.com", "paypal.pl", "bik.pl", "krd.pl", "erif.pl", "big.pl",
    "bossa.pl", "xtb.com", "xtb.pl", "pzu.pl", "warta.pl", "ergohestia.pl", "allianz.pl",
    "generali.pl", "uniqa.pl", "link4.pl", "compensa.pl", "aviva.pl", "nn.pl", "pocztapolska.pl",
    "poczta-polska.pl", "blik.com", "przelewy24.pl", "payu.pl", "tpay.com",
]

DOMENY_SPOLECZNOSCIOWE = [
    "facebookmail.com", "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "tiktok.com", "pinterest.com", "youtube.com", "reddit.com", "redditmail.com",
    "quora.com", "nk.pl", "goldenline.pl", "snapchat.com", "discord.com", "threads.net",
]

# Darmowe skrzynki prywatne - nadawca z takiej domeny mo�e by� osob� prywatn�.
DOMENY_PRYWATNE = [
    "gmail.com", "googlemail.com", "wp.pl", "onet.pl", "onet.eu", "op.pl", "o2.pl", "tlen.pl", "interia.pl",
    "interia.eu", "int.pl", "poczta.fm", "vp.pl", "gazeta.pl", "go2.pl", "autograf.pl", "buziaczek.pl",
    "yahoo.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "me.com", "aol.com", "gmx.com",
    "gmx.de", "proton.me", "protonmail.com", "spoko.pl", "poczta.onet.pl",
]

ZNACZNIKI_ESP = [
    "mailchimp", "mcsv.net", "mcdlv.net", "rsgsv.net", "sendgrid", "mailgun", "amazonses",
    "sparkpost", "salesmanago", "freshmail", "getresponse", "sendinblue", "brevo", "mailjet",
    "hubspot", "klaviyo", "exacttarget", "salesforce", "smtp2go", "mandrill", "ecomail",
    "redlink", "emaillabs", "mailerlite", "convertkit", "constantcontact", "campaign-archive",
    "edrone", "ipresso", "synerise", "sare.pl", "newsletter", "mailing", "bulk", "mta",
    "emarsys", "selligent", "responsys", "acoustic", "cheetahmail", "moosend", "omnisend",
]

# --- S�owa kluczowe (po normalizacji: ma�e litery, bez polskich znak�w) ----

SLOWA_CHRONIONE = [
    r"faktur\w*", r"fv", r"e-?faktur\w*", r"rachun\w*", r"umow\w*", r"aneks\w*", r"polis\w*",
    r"wezwani\w*", r"pit(-?\d+)?", r"ksef", r"wyrok\w*", r"postanowieni\w*", r"pozew\w*",
    r"nakaz\w*", r"decyzj\w*", r"zawiadomieni\w*", r"e-?doreczeni\w*", r"epuap", r"upomnieni\w*",
    r"komornik\w*", r"termin\w* platnosci", r"wyciag\w*", r"potwierdzenie przelewu",
    r"zaleglosc\w*", r"windykac\w*", r"rozliczeni\w*", r"deklaracj\w*", r"zus", r"urzad\w*",
    r"sad(u|owi|em|zie|y|ow|ach|ami)?", r"sadow\w*", r"notari\w*", r"pelnomocnictw\w*", r"invoice\w*", r"contract\w*",
    r"agreement", r"paragon\w*", r"kredyt\w* hipoteczn\w*", r"harmonogram splat",
    r"nota (obciazeniowa|ksiegowa|korygujaca)", r"korekt\w* faktur\w*", r"pismo", r"egzekuc\w*",
    r"mandat\w*", r"wypowiedzeni\w*", r"reklamacj\w*", r"gwarancj\w*",
    r"przypomnieni\w* o (platnosc\w*|zaplac\w*|terminie)", r"do zaplaty", r"naleznosc\w*", r"zaplat\w* za",
    r"fs ?\d+", r"fv ?\d+", r"nota odsetkow\w*", r"saldo", r"splat\w*", r"kredyt\w*", r"pozyczk\w*",
]

SLOWA_PROMOCYJNE = [
    r"promocj\w*", r"rabat\w*", r"wyprzedaz\w*", r"black friday", r"cyber monday",
    r"kod rabatowy", r"kupon\w*", r"oferta specjalna", r"tylko dzis", r"ostatnia szansa",
    r"darmowa dostawa", r"okazj\w*", r"taniej", r"-\d{1,2} ?%", r"\d{1,2} ?% (taniej|rabatu|znizki)",
    r"znizk\w*", r"bestseller\w*", r"nowa kolekcja", r"sale", r"deal\w*", r"discount",
    r"przecen\w*", r"gratis", r"prezent\w*", r"wygraj", r"konkurs\w*", r"limitowan\w*",
]

SLOWA_TRANSAKCYJNE = [
    r"zamowieni\w*", r"przesylk\w*", r"paczk\w*", r"wysylk\w*", r"nadan\w*", r"odbior\w*",
    r"potwierdzeni\w* (zamowienia|rezerwacji|platnosci|zakupu)", r"rezerwacj\w*", r"bilet\w*",
    r"order", r"shipment", r"tracking", r"zakup\w*", r"platnosc\w*", r"zwrot\w*",
]

SLOWA_POWIADOMIEN = [
    r"powiadomieni\w*", r"przypomnieni\w*", r"nowa wiadomosc", r"nowe logowanie",
    r"zalogowano", r"kod weryfikacyjny", r"kod jednorazowy", r"notification", r"alert\w*",
    r"aktualizacj\w*", r"status", r"raport dzienny", r"podsumowanie", r"digest",
]

LOKALNE_NOREPLY = re.compile(
    r"^(no-?reply|do-?not-?reply|nie-?odpowiadaj|noreply|notifications?|powiadomienia|"
    r"alerts?|news(letter)?|marketing|mailing|promo(cje)?|oferty|automat|system|bounce|"
    r"mailer-daemon|postmaster)([-._+].*)?$"
)

ROZSZERZENIA_DOKUMENTOW = ("pdf", "xml", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods",
                          "xades", "p7s", "p7m", "sig", "asice", "zip", "jpk")


def normalizuj(tekst: str) -> str:
    tekst = (tekst or "").lower().replace("�", "l")
    tekst = unicodedata.normalize("NFKD", tekst)
    return "".join(c for c in tekst if not unicodedata.combining(c))


def _kompiluj(wzorce):
    return re.compile(r"(?<![\w])(" + "|".join(wzorce) + r")(?![\w])")


RE_CHRONIONE = _kompiluj(SLOWA_CHRONIONE)
RE_PROMO = _kompiluj(SLOWA_PROMOCYJNE)
RE_TRANS = _kompiluj(SLOWA_TRANSAKCYJNE)
RE_POWIAD = _kompiluj(SLOWA_POWIADOMIEN)


def domena(adres: str) -> str:
    adres = (adres or "").strip().lower()
    return adres.rsplit("@", 1)[1] if "@" in adres else ""


_SLD = {"com", "net", "org", "gov", "edu", "co", "waw", "biz", "info", "ac", "mil", "art",
        "media", "shop", "sklep", "auto", "nom"}


def domena_glowna(dom: str) -> str:
    czesci = [c for c in (dom or "").lower().split(".") if c]
    if len(czesci) >= 3 and len(czesci[-1]) == 2 and czesci[-2] in _SLD:
        return ".".join(czesci[-3:])
    return ".".join(czesci[-2:])


def _pasuje_domena(dom: str, lista) -> str | None:
    for d in lista:
        d = d.lower().lstrip("@.")
        if dom == d or dom.endswith("." + d):
            return d
    return None


@dataclass
class Ocena:
    kategoria: str
    chroniona: bool
    powody_ochrony: list = field(default_factory=list)
    powody: list = field(default_factory=list)
    masowa: bool = False
    zablokowany: bool = False
    pewnosc: str = "niska"   # pewno��, �e to zb�dna wiadomo��: wysoka / srednia / niska


def ocen(rec: dict, *, folder: str = "INBOX", korespondenci=frozenset(), chronieni=(),
         zablokowani=(), dodatkowe_domeny=(), dodatkowe_slowa=(), folder_spam=False,
         masowi=frozenset()) -> Ocena:
    """Klasyfikuje wiadomo�� na podstawie zapisanych nag��wk�w."""
    n = rec.get("n", {})
    od = (rec.get("od") or "").lower()
    dom = domena(od)
    lokalna = od.split("@", 1)[0] if "@" in od else od
    temat = normalizuj(rec.get("temat", ""))

    powody_ochrony: list[str] = []
    powody: list[str] = []

    # --- twarda ochrona ---
    if od and (od in chronieni or _pasuje_domena(dom, [c for c in chronieni if "@" not in c])):
        powody_ochrony.append("na Twojej li�cie chronionych")
    if d := _pasuje_domena(dom, DOMENY_URZEDOWE):
        powody_ochrony.append(f"nadawca urz�dowy/prawny ({d})")
    if d := _pasuje_domena(dom, list(DOMENY_FINANSOWE) + list(dodatkowe_domeny)):
        powody_ochrony.append(f"instytucja finansowa lub chroniona domena ({d})")
    if m := RE_CHRONIONE.search(temat):
        powody_ochrony.append(f'temat zawiera "{m.group(0)}"')
    for slowo in dodatkowe_slowa:
        if normalizuj(slowo) and normalizuj(slowo) in temat:
            powody_ochrony.append(f'temat zawiera "{slowo}"')
    if rec.get("zalaczniki"):
        powody_ochrony.append("za��cznik-dokument (" + ", ".join(sorted(set(rec["zalaczniki"]))[:4]) + ")")
    if od and od in korespondenci:
        powody_ochrony.append("pisa�e�(-a�) do tego nadawcy (folder Wys�ane)")
    if rec.get("oznaczona"):
        powody_ochrony.append("oznaczona flag�/gwiazdk�")

    # --- cechy wysy�ki masowej ---
    esp = ""
    zrodla = " ".join(str(n.get(k, "")) for k in ("return-path", "feedback-id", "x-mailer", "list-id",
                                                  "x-campaign", "x-mailgun-tag", "x-sg-eid",
                                                  "x-ses-outgoing")).lower()
    for z in ZNACZNIKI_ESP:
        if z in zrodla:
            esp = z
            break
    lista_wypis = bool(n.get("list-unsubscribe"))
    precedence = str(n.get("precedence", "")).lower().strip()
    auto = str(n.get("auto-submitted", "")).lower().strip()
    noreply = bool(LOKALNE_NOREPLY.match(lokalna))
    masowa = bool(lista_wypis or n.get("list-id") or precedence in ("bulk", "list", "junk") or esp)
    if lista_wypis:
        powody.append("nag��wek List-Unsubscribe")
    if n.get("list-id"):
        powody.append("nag��wek List-Id")
    if precedence in ("bulk", "list", "junk"):
        powody.append(f"Precedence: {precedence}")
    if esp:
        powody.append(f"system mailingowy ({esp})")
    if noreply:
        powody.append(f"adres automatyczny ({lokalna}@)")

    prywatna_domena = bool(_pasuje_domena(dom, DOMENY_PRYWATNE))
    # Firma bez nag��wk�w mailingowych, ale wysy�aj�ca dziesi�tki wiadomo�ci, na kt�re nigdy nie
    # odpowiada�e� - to te� wysy�ka masowa (nie dotyczy darmowych skrzynek prywatnych).
    if not masowa and od in masowi and od not in korespondenci and not prywatna_domena:
        masowa = True
        powody.append("firma wys�a�a dziesi�tki podobnych wiadomo�ci")

    spam_flag = str(n.get("x-spam-flag", "")).strip().lower().startswith("yes") or \
        str(n.get("x-spam-status", "")).strip().lower().startswith("yes")

    # --- kategoria ---
    spoleczn = _pasuje_domena(dom, DOMENY_SPOLECZNOSCIOWE)
    if spam_flag or folder_spam:
        kat = SPAM
        powody.insert(0, "oznaczona jako spam przez serwer" if spam_flag else "w folderze Spam")
    elif spoleczn:
        kat = SPOLECZNOSCIOWE
        powody.insert(0, f"serwis spo�eczno�ciowy ({spoleczn})")
    elif masowa:
        if m := RE_PROMO.search(temat):
            kat = REKLAMA
            powody.insert(0, f'promocja w temacie ("{m.group(0)}")')
        elif RE_TRANS.search(temat):
            kat = TRANSAKCYJNE
            powody.insert(0, "temat transakcyjny")
        else:
            kat = NEWSLETTER
    elif not prywatna_domena and (m := RE_PROMO.search(temat)):
        kat = REKLAMA
            powody.insert(0, f'promocja w temacie ("{m.group(0)}")')
    elif RE_TRANS.search(temat):
        kat = TRANSAKCYJNE
        powody.insert(0, "temat transakcyjny")
    elif auto.startswith("auto") or noreply or RE_POWIAD.search(temat):
        kat = POWIADOMIENIE
        if auto:
            powody.append(f"Auto-Submitted: {auto}")
    elif od:
        kat = OSOBISTE
        powody.insert(0, "brak cech wysy�ki masowej")
    else:
        kat = INNE

    # s�aba ochrona: wygl�daj�ca na prywatn� korespondencja (nie dotyczy spamu)
    if kat == OSOBISTE and not spam_flag and not folder_spam:
        powody_ochrony.append("mo�liwa korespondencja prywatna (brak cech wysy�ki masowej)")

    zablokowany = bool(od and (od in zablokowani or _pasuje_domena(dom, [z for z in zablokowani if "@" not in z])))
    if zablokowany:
        powody.insert(0, "nadawca na Twojej li�cie blokowanych")

    # Pewno��: ile NIEZALE�NYCH sygna��w potwierdza, �e to wysy�ka masowa/reklamowa.
    sygnaly = sum([lista_wypis, bool(n.get("list-id")), precedence in ("bulk", "list", "junk"), bool(esp),
                   od in masowi, noreply, bool(RE_PROMO.search(temat))])
    if spam_flag or zablokowany or sygnaly >= 2:
        pewnosc = "wysoka"
    elif sygnaly == 1 or folder_spam:
        pewnosc = "srednia"
    else:
        pewnosc = "niska"

    return Ocena(kategoria=kat, chroniona=bool(powody_ochrony), powody_ochrony=powody_ochrony,
                 powody=powody, masowa=masowa, zablokowany=zablokowany, pewnosc=pewnosc)


ZACHOWAJ, KWARANTANNA, ARCHIWUM, ZOSTAW = "zachowaj", "kwarantanna", "archiwum", "zostaw"


def wiek_dni(rec: dict, teraz: datetime | None = None) -> float:
    teraz = teraz or datetime.now(timezone.utc)
    try:
        d = datetime.fromisoformat(rec["data"])
    except Exception:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (teraz - d).total_seconds() / 86400


def rekomendacja(rec: dict, ocena: Ocena, zasady: dict, teraz: datetime | None = None) -> tuple[str, str]:
    """Zwraca (akcja, uzasadnienie). Chronione zawsze -> zachowaj."""
    if ocena.chroniona:
        return ZACHOWAJ, "chroniona: " + "; ".join(ocena.powody_ochrony)
    wiek = wiek_dni(rec, teraz)
    k = ocena.kategoria
    if ocena.zablokowany:
        return KWARANTANNA, "nadawca zablokowany"
    if k == SPAM:
        return KWARANTANNA, "spam"
    progi = {
        REKLAMA: zasady.get("reklama_starsze_niz_dni", 30),
        NEWSLETTER: zasady.get("newsletter_starsze_niz_dni", 60),
        POWIADOMIENIE: zasady.get("powiadomienia_starsze_niz_dni", 30),
        SPOLECZNOSCIOWE: zasady.get("spolecznosciowe_starsze_niz_dni", 14),
    }
    if k in progi:
        if wiek >= progi[k]:
            return KWARANTANNA, f"{k}, starsza ni� {progi[k]} dni"
        return ZOSTAW, f"{k}, �wie�a ({int(wiek)} dni)"
    if k == TRANSAKCYJNE:
        prog = zasady.get("transakcyjne_archiwum_po_dniach", 180)
        if wiek >= prog:
            return ARCHIWUM, f"transakcyjna, starsza ni� {prog} dni"
        return ZOSTAW, "transakcyjna, aktualna"
    return ZOSTAW, "brak powodu do zmian"
