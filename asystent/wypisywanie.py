"""Wypisywanie z list mailingowych (wykonywane WY��CZNIE z CLI po Twojej zgodzie).

Kolejno�� metod:
1. RFC 8058 "one-click" (POST na adres https z nag��wka List-Unsubscribe),
2. e-mail na adres mailto: z nag��wka,
3. je�li jest tylko zwyk�y link https - nie otwieramy go automatycznie,
   tylko pokazujemy Tobie do r�cznego otwarcia.
Link�w z TRE�CI wiadomo�ci nie u�ywamy nigdy.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage


def metody(list_unsubscribe: str, list_unsubscribe_post: str = "") -> dict:
    adresy = re.findall(r"<\s*([^>]+?)\s*>", list_unsubscribe or "")
    if not adresy and list_unsubscribe:
        adresy = [a.strip() for a in list_unsubscribe.split(",")]
    https = [a for a in adresy if a.lower().startswith("https://")]
    mailto = [a for a in adresy if a.lower().startswith("mailto:")]
    one_click = bool(https) and "list-unsubscribe=one-click" in (list_unsubscribe_post or "").lower().replace(" ", "")
    if one_click:
        rodzaj = "one-click"
    elif mailto:
        rodzaj = "mailto"
    elif https:
        rodzaj = "link-reczny"
    else:
        rodzaj = "brak"
    return {"rodzaj": rodzaj, "https": https[:1], "mailto": mailto[:1]}


def one_click(url: str, timeout: int = 20) -> tuple[bool, str]:
    if not url.lower().startswith("https://"):
        return False, "dozwolone s� tylko adresy https"
    req = urllib.request.Request(
        url, data=b"List-Unsubscribe=One-Click", method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mail-Unsubscribe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except Exception as e:
        return False, f"b��d: {e}"


def mailto(adres_mailto: str, nadawca: str, smtp_host: str, smtp_port: int, haslo: str) -> tuple[bool, str]:
    u = urllib.parse.urlparse(adres_mailto)
    do = urllib.parse.unquote(u.path)
    q = urllib.parse.parse_qs(u.query)
    if "@" not in do:
        return False, "niepoprawny adres mailto"
    msg = EmailMessage()
    msg["From"] = nadawca
    msg["To"] = do
    msg["Subject"] = q.get("subject", ["unsubscribe"])[0]
    msg.set_content(q.get("body", ["unsubscribe"])[0])
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(nadawca, haslo)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(nadawca, haslo)
                s.send_message(msg)
        return True, f"wys�ano pro�b� na {do}"
    except Exception as e:
        return False, f"b��d SMTP: {e}"
