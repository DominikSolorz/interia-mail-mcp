"""Cienka, bezpieczna nak�adka na imaplib.

Celowo NIE ma tu operacji trwa�ego usuwania ca�ej skrzynki. Jedyne EXPUNGE to
UID EXPUNGE ograniczone do konkretnych UID po udanym skopiowaniu (fallback,
gdy serwer nie obs�uguje MOVE) albo po jawnym potwierdzeniu w CLI.
"""

from __future__ import annotations

import email
import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import getaddresses, parseaddr

from . import utf7
from .klasyfikator import ROZSZERZENIA_DOKUMENTOW

POLA_NAGLOWKOW = (
    "FROM TO CC SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST LIST-ID "
    "PRECEDENCE AUTO-SUBMITTED X-MAILER RETURN-PATH REPLY-TO FEEDBACK-ID X-CAMPAIGN "
    "X-SPAM-FLAG X-SPAM-STATUS X-MAILGUN-TAG X-SG-EID X-SES-OUTGOING"
)
ZAPISYWANE = [p.lower() for p in POLA_NAGLOWKOW.split()
              if p not in ("FROM", "TO", "CC", "SUBJECT", "DATE", "MESSAGE-ID")]

_MIESIACE = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class BladImap(Exception):
    pass


def cytuj(nazwa: str) -> str:
    return '"' + nazwa.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _internaldate(s: str) -> str:
    # "13-Nov-2026 10:22:01 +0100"
    try:
        dzien, mies, reszta = s.split("-", 2)
        rok, czas, strefa = reszta.split(" ")
        hh, mm, ss = (int(x) for x in czas.split(":"))
        znak = 1 if strefa[0] == "+" else -1
        off = timedelta(hours=int(strefa[1:3]), minutes=int(strefa[3:5])) * znak
        d = datetime(int(rok), _MIESIACE[mies], int(dzien), hh, mm, ss, tzinfo=timezone(off))
        return d.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _h(msg, nazwa: str) -> str:
    try:
        v = msg.get(nazwa)
        return "" if v is None else " ".join(str(v).split())
    except Exception:
        try:
            raw = msg.get_all(nazwa, failobj=[""], )[0]
            return " ".join(str(raw).split())
        except Exception:
            return ""


def _adresy(msg, *nazwy) -> list[str]:
    wartosci = []
    for n in nazwy:
        try:
            wartosci += [str(v) for v in (msg.get_all(n) or [])]
        except Exception:
            pass
    return [a.lower() for _, a in getaddresses(wartosci) if "@" in a]


ROZSZERZENIA_OBRAZOW = ("jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "bmp", "gif", "webp")


def zalaczniki_z_bodystructure(meta: str) -> list[str]:
    """Typy prawdziwych za��cznik�w. Dokumenty licz� si� zawsze; obrazy tylko wtedy, gdy s�
    do��czone jako za��cznik (disposition "attachment"), a nie jako grafika w tre�ci reklamy."""
    meta_l = meta.lower()
    znalezione = []
    for ext in ROZSZERZENIA_DOKUMENTOW:
        if re.search(r'\.' + ext + r'"', meta_l):
            znalezione.append(ext)
    if '"application" "pdf"' in meta_l and "pdf" not in znalezione:
        znalezione.append("pdf")
    for m in re.finditer(r'\("attachment" \(([^)]*)\)', meta_l):
        for ext in re.findall(r'\.([a-z0-9]{2,5})"', m.group(1)):
            if ext not in znalezione:
                znalezione.append(ext)
    if '("attachment"' in meta_l and not znalezione:
        znalezione.append("plik")
    return znalezione


def nazwy_zalacznikow(meta: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'"(?:filename|name)\*?" "([^"]{1,120})"', meta, flags=re.I)))[:10]


def parsuj_fetch(dane) -> list[dict]:
    """Zamienia odpowied� imaplib (UID FETCH) na list� rekord�w."""
    surowe = []
    biezacy = None
    for el in dane:
        if isinstance(el, tuple):
            biezacy = {"meta": el[0], "lit": el[1]}
            surowe.append(biezacy)
        elif isinstance(el, bytes) and biezacy is not None:
            biezacy["meta"] += el
    wyniki = []
    for s in surowe:
        meta = s["meta"].decode("utf-8", "replace")
        m_uid = re.search(r"UID (\d+)", meta)
        if not m_uid:
            continue
        m_flags = re.search(r"FLAGS \(([^)]*)\)", meta)
        flagi = m_flags.group(1).split() if m_flags else []
        m_date = re.search(r'INTERNALDATE "([^"]+)"', meta)
        m_size = re.search(r"RFC822\.SIZE (\d+)", meta)
        msg = email.message_from_bytes(s["lit"] or b"", policy=policy.default)
        od_nazwa, od = parseaddr(_h(msg, "From"))
        wyniki.append({
            "uid": int(m_uid.group(1)),
            "message_id": _h(msg, "Message-ID"),
            "od": od.lower(),
            "od_nazwa": od_nazwa,
            "do": _adresy(msg, "To", "Cc"),
            "temat": _h(msg, "Subject"),
            "data": _internaldate(m_date.group(1)) if m_date else datetime.now(timezone.utc).isoformat(),
            "przeczytana": "\\Seen" in flagi,
            "oznaczona": "\\Flagged" in flagi,
            "rozmiar": int(m_size.group(1)) if m_size else 0,
            "zalaczniki": zalaczniki_z_bodystructure(meta),
            "zalaczniki_nazwy": nazwy_zalacznikow(meta),
            "n": {k: _h(msg, k) for k in ZAPISYWANE if _h(msg, k)},
        })
    return wyniki


def parsuj_list(linia) -> dict | None:
    if isinstance(linia, tuple):
        linia = linia[0] + b'"' + linia[1] + b'"'
    if not linia:
        return None
    s = linia.decode("utf-8", "replace")
    m = re.match(r'\((?P<flagi>[^)]*)\) (?P<sep>"[^"]*"|NIL) (?P<nazwa>.+)$', s)
    if not m:
        return None
    nazwa = m.group("nazwa").strip()
    if nazwa.startswith('"') and nazwa.endswith('"'):
        nazwa = nazwa[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return {"nazwa": utf7.decode(nazwa), "flagi": m.group("flagi").split(),
            "separator": m.group("sep").strip('"')}


class Klient:
    """Po��czenie IMAP z Interi�. U�ywaj jako mened�era kontekstu."""

    def __init__(self, host: str, port: int, uzytkownik: str, haslo: str, timeout: int = 180):
        self.host, self.port, self.uzytkownik, self._haslo, self.timeout = host, port, uzytkownik, haslo, timeout
        self.conn: imaplib.IMAP4_SSL | None = None
        self.mozliwosci: set[str] = set()
        self._wybrany: tuple[str, bool] | None = None

    def __enter__(self):
        ctx = ssl.create_default_context()
        try:
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx, timeout=self.timeout)
            self.conn.login(self.uzytkownik, self._haslo)
        except imaplib.IMAP4.error as e:
            raise BladImap(f"Logowanie nieudane: {e}. Sprawd�, czy w Interii w��czono dost�p IMAP "
                           "(Ustawienia  Ustawienia g��wne  Parametry) i czy has�o jest poprawne.") from e
        except OSError as e:
            raise BladImap(f"Brak po��czenia z {self.host}:{self.port}: {e}") from e
        typ, dane = self.conn.capability()
        self.mozliwosci = set(dane[0].decode().upper().split()) if dane and dane[0] else set()
        return self

    def __exit__(self, *exc):
        try:
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn.logout()
        except Exception:
            pass

    # --- foldery ---
    def foldery(self) -> list[dict]:
        typ, dane = self.conn.list()
        if typ != "OK":
            raise BladImap("Nie uda�o si� pobra� listy folder�w")
        return [f for f in (parsuj_list(l) for l in dane) if f]

    def utworz_folder(self, nazwa: str) -> None:
        istniejace = {f["nazwa"] for f in self.foldery()}
        if nazwa in istniejace:
            return
        typ, dane = self.conn.create(cytuj(utf7.encode(nazwa)))
        if typ != "OK":
            raise BladImap(f"Nie uda�o si� utworzy� folderu {nazwa}: {dane}")
        try:
            self.conn.subscribe(cytuj(utf7.encode(nazwa)))
        except Exception:
            pass

    def wybierz(self, folder: str, tylko_odczyt: bool = True) -> tuple[int, int]:
        typ, dane = self.conn.select(cytuj(utf7.encode(folder)), readonly=tylko_odczyt)
        if typ != "OK":
            raise BladImap(f"Nie mo�na otworzy� folderu {folder}: {dane}")
        self._wybrany = (folder, tylko_odczyt)
        liczba = int(dane[0]) if dane and dane[0] else 0
        _, uv = self.conn.response("UIDVALIDITY")
        uidvalidity = int(uv[0]) if uv and uv[0] else 0
        return liczba, uidvalidity

    def _wznow_polaczenie(self) -> None:
        """Wznawia połączenie po rozłączeniu serwera podczas długiego skanu."""
        try:
            if self.conn is not None:
                self.conn.logout()
        except Exception:
            pass
        ctx = ssl.create_default_context()
        self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx, timeout=self.timeout)
        self.conn.login(self.uzytkownik, self._haslo)
        typ, dane = self.conn.capability()
        self.mozliwosci = set(dane[0].decode().upper().split()) if dane and dane[0] else set()
        if self._wybrany:
            self.wybierz(self._wybrany[0], tylko_odczyt=self._wybrany[1])

    # --- odczyt ---
    def uidy(self, folder: str) -> tuple[int, list[int]]:
        liczba, uv = self.wybierz(folder)
        if liczba == 0:
            return uv, []
        typ, dane = self.conn.uid("SEARCH", None, "ALL")
        if typ != "OK":
            raise BladImap("UID SEARCH nie powi�d� si�")
        return uv, [int(x) for x in (dane[0] or b"").split()]

    def uidy_z_naglowkiem(self, folder: str, naglowek: str = "List-Unsubscribe") -> tuple[int, list[int]]:
        """Wyszukuje UID-y po stronie serwera IMAP bez pobierania całego folderu."""
        uv, _ = self.uidy(folder)
        typ, dane = self.conn.uid("SEARCH", None, "HEADER", naglowek, '""')
        if typ != "OK":
            raise BladImap(f"Wyszukiwanie nagłówka {naglowek} nie powiodło się: {dane}")
        return uv, [int(x) for x in (dane[0] or b"").split()]

    def flagi(self, folder: str) -> dict[int, list[str]]:
        liczba, _ = self.wybierz(folder)
        if liczba == 0:
            return {}
        typ, dane = self.conn.uid("FETCH", "1:*", "(UID FLAGS)")
        wynik = {}
        for el in dane:
            s = el.decode("utf-8", "replace") if isinstance(el, bytes) else el[0].decode("utf-8", "replace")
            m_uid = re.search(r"UID (\d+)", s)
            m_f = re.search(r"FLAGS \(([^)]*)\)", s)
            if m_uid:
                wynik[int(m_uid.group(1))] = m_f.group(1).split() if m_f else []
        return wynik

    def naglowki(self, folder: str, uidy: list[int], partia: int = 500, tylko_adresaci: bool = False):
        self.wybierz(folder)
        pola = "TO CC" if tylko_adresaci else POLA_NAGLOWKOW
        for i in range(0, len(uidy), partia):
            zestaw = ",".join(str(u) for u in uidy[i:i + partia])
            elementy = "(UID FLAGS INTERNALDATE RFC822.SIZE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (%s)])" % pola
            if tylko_adresaci:
                elementy = "(UID BODY.PEEK[HEADER.FIELDS (%s)])" % pola
            try:
                typ, dane = self.conn.uid("FETCH", zestaw, elementy)
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                self._wznow_polaczenie()
                typ, dane = self.conn.uid("FETCH", zestaw, elementy)
            if typ != "OK":
                raise BladImap(f"Pobieranie nagłówków nie powiodło się: {dane}")
            yield from parsuj_fetch(dane)

    def cala_wiadomosc(self, folder: str, uid: int, maks_bajtow: int = 3_000_000) -> bytes | None:
        self.wybierz(folder)
        typ, dane = self.conn.uid("FETCH", str(uid), "(RFC822.SIZE)")
        m = re.search(rb"RFC822\.SIZE (\d+)", dane[0] if dane and dane[0] else b"")
        if m and int(m.group(1)) > maks_bajtow:
            return None
        typ, dane = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        for el in dane:
            if isinstance(el, tuple):
                return el[1]
        return None

    def szukaj_message_id(self, folder: str, message_id: str) -> list[int]:
        self.wybierz(folder)
        mid = message_id.strip()
        typ, dane = self.conn.uid("SEARCH", None, "HEADER", "Message-ID", cytuj(mid))
        if typ != "OK":
            return []
        return [int(x) for x in (dane[0] or b"").split()]

    # --- zmiany (zawsze odwracalne przeniesienie) ---
    def przenies(self, folder: str, uidy: list[int], cel: str) -> int:
        if not uidy:
            return 0
        self.wybierz(folder, tylko_odczyt=False)
        przeniesione = 0
        for i in range(0, len(uidy), 200):
            czesc = uidy[i:i + 200]
            zestaw = ",".join(str(u) for u in czesc)
            if "MOVE" in self.mozliwosci:
                typ, dane = self.conn.uid("MOVE", zestaw, cytuj(utf7.encode(cel)))
                if typ != "OK":
                    raise BladImap(f"MOVE nie powi�d� si�: {dane}")
            else:
                if "UIDPLUS" not in self.mozliwosci:
                    raise BladImap("Serwer nie obs�uguje MOVE ani UIDPLUS - przenoszenie wy��czone "
                                   "dla bezpiecze�stwa (nie usuwam niczego bez gwarancji).")
                typ, dane = self.conn.uid("COPY", zestaw, cytuj(utf7.encode(cel)))
                if typ != "OK":
                    raise BladImap(f"COPY nie powi�d� si�: {dane}")
                self.conn.uid("STORE", zestaw, "+FLAGS.SILENT", r"(\Deleted)")
                typ, dane = self.conn.uid("EXPUNGE", zestaw)
                if typ != "OK":
                    self.conn.uid("STORE", zestaw, "-FLAGS.SILENT", r"(\Deleted)")
                    raise BladImap("UID EXPUNGE nie powi�d� si� - cofni�to oznaczenie.")
            przeniesione += len(czesc)
        return przeniesione

    def usun_trwale(self, folder: str, uidy: list[int]) -> int:
        """Trwa�e usuni�cie wskazanych UID. Wywo�ywane WY��CZNIE z CLI po potwierdzeniu."""
        if not uidy:
            return 0
        if "UIDPLUS" not in self.mozliwosci:
            raise BladImap("Serwer nie obs�uguje UID EXPUNGE - trwa�e usuwanie niedost�pne.")
        self.wybierz(folder, tylko_odczyt=False)
        zestaw = ",".join(str(u) for u in uidy)
        self.conn.uid("STORE", zestaw, "+FLAGS.SILENT", r"(\Deleted)")
        typ, dane = self.conn.uid("EXPUNGE", zestaw)
        if typ != "OK":
            raise BladImap(f"UID EXPUNGE nie powi�d� si�: {dane}")
        return len(uidy)
