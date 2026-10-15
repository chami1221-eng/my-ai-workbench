# -*- coding: utf-8 -*-
"""Extract "load-bearing values" from a memory note: money amounts, pipeline stages,
record IDs. Notations are normalized so that 460,800 JPY / ¥460,800 / 46.08万 compare equal.
"""
import re

import guard_config as C

MONEY = re.compile(
    r"[¥￥]\s?(\d[\d,]*(?:\.\d+)?)\s*([MK万億]?)"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(億|万)?円"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(億|万)(?![人件名本枚回])"
    r"|\$\s?(\d[\d,]*(?:\.\d+)?)\s*([MK]?)"
)
STAGE = re.compile(r"Stage\s?[:：]?\s?0?(\d{1,2})", re.I)
RECORD_ID = re.compile(C.RECORD_ID_RE)
UNIT = {"": 1, "K": 1_000, "M": 1_000_000, "万": 10_000, "億": 100_000_000}


def _num(s, unit):
    return round(float(s.replace(",", "")) * UNIT[unit or ""])


def values(text):
    """Map of normalized key -> first raw occurrence."""
    out = {}
    for m in MONEY.finditer(text):
        yen1, u1, yen2, u2, yen3, u3, usd, u4 = m.groups()
        if yen1:
            key = ("jpy", _num(yen1, u1))
        elif yen2:
            key = ("jpy", _num(yen2, u2))
        elif yen3:
            key = ("jpy", _num(yen3, u3))
        else:
            key = ("usd", _num(usd, u4))
        if key[1] >= 1000:   # drop fragments like "¥3"
            out.setdefault(key, m.group(0).strip())
    for m in STAGE.finditer(text):
        out.setdefault(("stage", int(m.group(1))), m.group(0))
    for m in RECORD_ID.finditer(text):
        out.setdefault(("id", m.group(0)), m.group(0))
    return out
