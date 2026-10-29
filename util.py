# -*- coding: utf-8 -*-
"""Funções de apoio: log e normalizações."""
import re
import unicodedata
from datetime import datetime, timezone


def log(msg):
    agora = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{agora}] {msg}", flush=True)


def sem_acentos(texto):
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def nome_para_codigo(nome):
    """Primeiro nome do cliente em maiúsculas, só letras e números (máx. 12)."""
    primeiro = (nome or "").strip().split(" ")[0]
    limpo = "".join(c for c in sem_acentos(primeiro).upper() if c.isalnum())
    return limpo[:12] or "CLIENTE"


def numero(texto, padrao=0.0):
    try:
        return float(str(texto).strip().replace(",", "."))
    except (TypeError, ValueError):
        return padrao


def inteiro(texto, padrao=0):
    try:
        return int(str(texto).strip())
    except (TypeError, ValueError):
        return padrao


def parse_data_vtex(texto):
    """Data ISO da VTEX (frações de 7 dígitos) -> datetime com fuso."""
    t = re.sub(r"\.(\d{1,6})\d*", r".\1", texto or "")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
