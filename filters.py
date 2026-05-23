"""
Helpers de filtro de voos. Hoje: janela de horario de chegada no destino.

A janela e' configurada em routes.yaml em monitoring.arrival_window:
  arrival_window:
    start: "08:00"
    end:   "12:00"

Quando ausente, in_arrival_window() retorna True para qualquer voo (sem filtro).
"""
from typing import Optional


def _hhmm(s: str) -> Optional[str]:
    """Extrai HH:MM de uma string ISO/local time. Retorna None se nao reconhecer."""
    if not s:
        return None
    t = str(s).strip()
    if " " in t:
        t = t.split(" ")[-1]
    if "T" in t:
        t = t.split("T")[-1]
    t = t[:5]
    if len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit():
        return t
    return None


def in_arrival_window(arrival_str, window: Optional[dict]) -> bool:
    """
    Retorna True se arrival_str cai na janela [start, end] (inclusivo).
    Sem janela configurada -> True (nao filtra).
    arrival_str invalido com janela ativa -> False.
    """
    if not window or not window.get("start") or not window.get("end"):
        return True
    hm = _hhmm(arrival_str)
    if hm is None:
        return False
    return window["start"] <= hm <= window["end"]


def has_window(config: dict) -> bool:
    w = (config.get("monitoring") or {}).get("arrival_window") or {}
    return bool(w.get("start") and w.get("end"))


def window_label(config: dict) -> str:
    w = (config.get("monitoring") or {}).get("arrival_window") or {}
    if w.get("start") and w.get("end"):
        return f"{w['start']}-{w['end']}"
    return ""


_CURRENCY_SYMBOLS = {
    "BRL": "R$",
    "EUR": "€",
    "USD": "US$",
    "GBP": "£",
    "ISK": "kr",
}


def currency_symbol(code: str) -> str:
    """Devolve simbolo amigavel para o codigo ISO; fallback = proprio codigo."""
    if not code:
        return ""
    return _CURRENCY_SYMBOLS.get(code.upper(), code.upper())

