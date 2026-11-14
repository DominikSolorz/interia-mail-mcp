"""Szyfrowanie sekretów użytkowników Interia."""
from __future__ import annotations
import os
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _klucz() -> bytes:
    raw = os.environ.get("INTERIA_ENCRYPTION_KEY", "")
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError("INTERIA_ENCRYPTION_KEY musi być 64-znakowym kluczem hex") from exc
    if len(key) != 32:
        raise RuntimeError("INTERIA_ENCRYPTION_KEY musi mieć 32 bajty")
    return key

def szyfruj(tekst: str, aad: str = "") -> str:
    nonce = secrets.token_bytes(12)
    dane = AESGCM(_klucz()).encrypt(nonce, tekst.encode("utf-8"), aad.encode("utf-8"))
    return (nonce + dane).hex()

def odszyfruj(token: str, aad: str = "") -> str:
    blob = bytes.fromhex(token)
    if len(blob) < 28:
        raise ValueError("Nieprawidłowy zaszyfrowany sekret")
    nonce, dane = blob[:12], blob[12:]
    return AESGCM(_klucz()).decrypt(nonce, dane, aad.encode("utf-8")).decode("utf-8")
