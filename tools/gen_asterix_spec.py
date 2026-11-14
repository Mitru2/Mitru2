"""Genereaza santinela/asterix_spec.py din asterix-specs (JSON): UAP-uri si structura completa a campurilor.

Utilizare: python tools/gen_asterix_spec.py cat001.json cat002.json ... > santinela/asterix_spec.py
JSON-urile: https://zoranbosnjak.github.io/asterix-specs/specs/catNNN/cats/catX.Y/definition.json
"""
import json, sys, pprint
from fractions import Fraction

def num(n):
    t = n["tag"]
    if t == "NumInt": return Fraction(n["contents"])
    if t == "NumPow": return Fraction(n["contents"]["base"]) ** n["contents"]["exponent"]
    if t == "NumDiv": return num(n["contents"]["numerator"]) / num(n["contents"]["denominator"])
    raise ValueError(t)

def rule_var(rule):
    if rule["tag"] == "ContextFree": return rule["contents"]
    if rule["tag"] == "Dependent":
        c = rule["contents"]
        return c.get("default") or c["cases"][0][1]
    raise ValueError(rule["tag"])

def content(c):
    t = c["tag"]
    if t == "ContentRaw": return ("raw",)
    if t == "ContentTable": return ("table",)
    if t == "ContentInteger":
        return ("int", c["contents"]["signedness"]["tag"] == "Signed")
    if t == "ContentQuantity":
        q = c["contents"]
        lsb = num(q["lsb"])
        return ("q", q["signedness"]["tag"] == "Signed", float(lsb), q.get("unit", ""))
    if t == "ContentString":
        return ("str", c["contents"]["tag"])  # StringAscii / StringICAO / StringOctal
    if t == "ContentBds":
        return ("raw",)
    raise ValueError(t)

def elements(var, prefix=""):
    """Lista plata de (nume, biti, continut) pentru Element/Group."""
    t = var["tag"]
    if t == "Element":
        r = var["contents"]["rule"]
        cont = content(r["contents"]) if r["tag"] == "ContextFree" else content((r["contents"].get("default") or r["contents"]["cases"][0][1]))
        return [(prefix.rstrip("."), var["contents"]["bitSize"], cont)]
    if t == "Group":
        out = []
        for it in var["contents"]:
            if it["tag"] == "Spare":
                out.append(("", it["contents"], ("spare",)))
            else:
                nm = it["contents"]["name"]
                out += elements(rule_var(it["contents"]["rule"]), nm + ".")
        return out
    raise ValueError(t)

def spec(var):
    t = var["tag"]
    if t in ("Element", "Group"):
        return ("F", elements(var))
    if t == "Extended":
        parts, cur = [], []
        for x in var["contents"]:
            if x is None:
                parts.append(cur); cur = []
            elif x["tag"] == "Spare":
                cur.append(("", x["contents"], ("spare",)))
            else:
                cur += elements(rule_var(x["contents"]["rule"]), x["contents"]["name"] + ".")
        if cur: parts.append(cur)
        return ("X", parts)
    if t == "Repetitive":
        typ = var["contents"]["type"]
        els = elements(var["contents"]["variation"])
        if typ["tag"] == "RepetitiveRegular":
            return ("R", typ["contents"]["byteSize"], els)
        return ("RF", els)
    if t == "Explicit":
        return ("EXP",)
    if t == "Compound":
        subs = []
        for x in var["contents"]:
            if x is None: subs.append(None)
            else: subs.append((x["name"], spec(rule_var(x["rule"]))))
        return ("C", subs)
    raise ValueError(t)

def uap_list(lst):
    return [u["contents"] if u["tag"] == "UapItem" else ("-" if u["tag"] == "UapItemSpare" else "RFS") for u in lst]

cats = {}
for path in sys.argv[1:]:
    d = json.load(open(path))["contents"]
    items = {it["name"]: (it["title"], spec(rule_var(it["rule"]))) for it in d["catalogue"]}
    u = d["uap"]
    if u["tag"] == "Uap":
        uaps, sel = {"default": uap_list(u["contents"])}, None
    else:
        uaps = {name: uap_list(lst) for name, lst in u["contents"]["cases"]}
        s = u["contents"]["selector"]
        sel = (s["item"], [list(c) for c in s["cases"]])
    cats[d["category"]] = {"edition": f'{d["edition"]["major"]}.{d["edition"]["minor"]}',
                           "date": f'{d["date"]["year"]}-{d["date"]["month"]:02d}-{d["date"]["day"]:02d}',
                           "title": d["title"], "uaps": uaps, "selector": sel, "items": items}

LICENSE = open(sys.argv[0].replace("gen_asterix_spec.py", "LICENSE.asterix-specs")).read()
print('"""Specificatii ASTERIX generate automat din proiectul asterix-specs (Zoran Bosnjak).')
print()
print("Sursa: https://zoranbosnjak.github.io/asterix-specs/  (revizia git 5cf64b635424891b9f7e5d8fc51f6531e5790f89)")
print("NU editati manual: regenerati cu instrumentul de generare. Structura unui articol:")
print('  ("F", elemente) fix | ("X", parti) extins cu FX | ("R", octeti_contor, elemente) repetitiv |')
print('  ("RF", elemente) repetitiv cu FX | ("EXP",) explicit | ("C", [(nume, articol) sau None]) compus.')
print('Element: (nume, biti, continut); continut: ("q", semn, lsb, unitate) | ("int", semn) | ("raw",) |')
print('  ("table",) | ("str", tip) | ("spare",).')
print()
print("Licenta specificatiilor structurate (BSD-3):")
print()
for line in LICENSE.rstrip().splitlines():
    print(("    " + line).rstrip())
print('"""')
print("# flake8: noqa")
print("CATEGORIES = ", end="")
pprint.pprint(cats, width=120, compact=True, sort_dicts=True)
