"""Ingredient lines: amounts, units, synonyms and unit systems (no database, no Flask)."""
import re

# ---------------------------------------------------------------- units

# unit -> (canonical unit, factor); grams and millilitres are summed and shown as kg / l when large
UNIT_ALIASES = {
    "g": ("g", 1), "gram": ("g", 1), "grams": ("g", 1), "gramme": ("g", 1), "grammes": ("g", 1),
    "kg": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000), "kilogram": ("g", 1000), "kilograms": ("g", 1000),
    "ml": ("ml", 1), "milliliter": ("ml", 1), "milliliters": ("ml", 1), "millilitre": ("ml", 1), "millilitres": ("ml", 1),
    "cl": ("ml", 10), "centiliter": ("ml", 10), "centiliters": ("ml", 10), "centilitre": ("ml", 10), "centilitres": ("ml", 10),
    "dl": ("ml", 100), "deciliter": ("ml", 100), "deciliters": ("ml", 100), "decilitre": ("ml", 100), "decilitres": ("ml", 100),
    "l": ("ml", 1000), "liter": ("ml", 1000), "liters": ("ml", 1000), "litre": ("ml", 1000), "litres": ("ml", 1000),
    "tsp": ("tsp", 1), "tsps": ("tsp", 1), "tsk": ("tsp", 1), "teaspoon": ("tsp", 1), "teaspoons": ("tsp", 1),
    "tbsp": ("tbsp", 1), "tbsps": ("tbsp", 1), "tbs": ("tbsp", 1), "tbl": ("tbsp", 1), "spsk": ("tbsp", 1),
    "tablespoon": ("tbsp", 1), "tablespoons": ("tbsp", 1),
    "cup": ("cup", 1), "cups": ("cup", 1),
    "oz": ("oz", 1), "ounce": ("oz", 1), "ounces": ("oz", 1),
    "lb": ("lb", 1), "lbs": ("lb", 1), "pound": ("lb", 1), "pounds": ("lb", 1),
    "stk": ("pcs", 1), "pcs": ("pcs", 1), "piece": ("pcs", 1), "pieces": ("pcs", 1),
    "can": ("can", 1), "cans": ("can", 1), "pack": ("pack", 1), "packs": ("pack", 1),
    "clove": ("clove", 1), "cloves": ("clove", 1), "pinch": ("pinch", 1), "pinches": ("pinch", 1),
    "bunch": ("bunch", 1), "bunches": ("bunch", 1), "handful": ("handful", 1), "handfuls": ("handful", 1),
    "slice": ("slice", 1), "slices": ("slice", 1), "sprig": ("sprig", 1), "sprigs": ("sprig", 1),
    "head": ("head", 1), "heads": ("head", 1),
}
PLURAL_UNITS = {"clove": "cloves", "pinch": "pinches", "bunch": "bunches", "handful": "handfuls", "slice": "slices",
                "sprig": "sprigs", "head": "heads", "can": "cans", "pack": "packs"}

# Only for the metric / imperial unit systems: ounces, pounds and cups are merged with grams and millilitres.
OZ_G, LB_G, CUP_ML, FLOZ_ML = 28.3495, 453.592, 240.0, 29.5735
TO_BASE = {"oz": ("g", OZ_G), "lb": ("g", LB_G), "cup": ("ml", CUP_ML)}

# the symbol a recipe used for a mass / volume (so the list can use the same one)
SYMBOL = {}
for _alias, (_base, _factor) in UNIT_ALIASES.items():
    if _base == "g" and _factor in (1, 1000):
        SYMBOL[_alias] = "g" if _factor == 1 else "kg"
    elif _base == "ml" and _factor in (1, 10, 100, 1000):
        SYMBOL[_alias] = {1: "ml", 10: "cl", 100: "dl", 1000: "l"}[_factor]
SYMBOL_FACTOR = {"g": 1, "kg": 1000, "ml": 1, "cl": 10, "dl": 100, "l": 1000}

FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d*\s*[½¼¾⅓⅔⅛]|\d+(?:[.,]\d+)?)"
# amount, optional range end ("3-4", "3 to 4"), rest of the line
QTY = re.compile(rf"^({NUM})(?:\s*(?:-|–|—|to)\s*({NUM}))?\s*(.*)$", re.I)
NOTE_COMMA = re.compile(r"(?<!\d),|,(?!\d)")  # a comma that is not a decimal comma
SIZE_WORDS = re.compile(r"^(?:(?:extra[- ])?(?:large|medium|small|big)\s+)+", re.I)

# ---------------------------------------------------------------- synonyms

# variant -> canonical (both singular). Users can add or override lines such as "red onion = onion";
# "red onion = red onion" keeps a built-in merge from happening.
BUILTIN_SYNONYMS = {
    "scallion": "spring onion", "green onion": "spring onion",
    "red onion": "onion", "yellow onion": "onion", "white onion": "onion", "brown onion": "onion",
    "cilantro": "coriander", "eggplant": "aubergine", "zucchini": "courgette", "arugula": "rocket",
    "ground beef": "minced beef", "ground pork": "minced pork", "ground turkey": "minced turkey",
    "ground chicken": "minced chicken", "ground lamb": "minced lamb",
    "capsicum": "bell pepper", "sweet pepper": "bell pepper",
    "garbanzo bean": "chickpea", "garbanzo": "chickpea", "chilli": "chili", "chile": "chili",
    "yoghurt": "yogurt", "shrimp": "prawn", "beet": "beetroot", "powdered sugar": "icing sugar",
}


def clean_ingredient(s):
    return re.sub(r"\s+", " ", s).strip().lstrip("-•* ").strip()


def singular(name):
    words = name.lower().split()
    if not words:
        return ""
    w = words[-1]
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("oes"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]
    words[-1] = w
    return " ".join(words)


def build_synonyms(lines=()):
    """Built-in synonyms plus the user's lines ('red onion = onion'); raises ValueError for a bad line."""
    syn = dict(BUILTIN_SYNONYMS)
    for line in lines:
        m = re.match(r"^\s*(.+?)\s*=\s*(.+?)\s*$", line)
        if not m:
            raise ValueError(f"Cannot read the synonym line “{line}”. Use for example: red onion = onion")
        syn[singular(clean_ingredient(m.group(1)))] = singular(clean_ingredient(m.group(2)))
    return syn


def canon_key(key, syn):
    for _ in range(3):  # follow short chains, never loop
        nxt = syn.get(key)
        if not nxt or nxt == key:
            break
        key = nxt
    return key


# ---------------------------------------------------------------- parsing

def to_number(s):
    s = s.strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", s) or re.fullmatch(r"()(\d+)/(\d+)", s)
    if m:
        den = int(m.group(3))
        return (int(m.group(1) or 0) + int(m.group(2)) / den) if den else None
    m = re.fullmatch(r"(\d*)\s*([½¼¾⅓⅔⅛])", s)
    if m:
        return int(m.group(1) or 0) + FRACTIONS[m.group(2)]
    return float(s.replace(",", "."))


def split_quantity(line, extra_units=()):
    """'2 cups of flour' -> ('flour', 2, 'cup', 'cup'); '3-4 cloves garlic' -> ('garlic', 4, 'clove', 'clove');
    '2 x 400 g tomatoes' -> ('tomatoes', 800, 'g', 'g'); 'Onion' -> ('Onion', None, '', '').
    The last item is the unit as the recipe wrote it ('dl' for '2 dl'), the third the canonical one ('ml', 200)."""
    m = QTY.match(line)
    if not m or not m.group(3).strip():
        return line, None, "", ""
    q = to_number(m.group(2) or m.group(1))  # for a range, buy enough: use the upper end
    if q is None:
        return line, None, "", ""
    rest = m.group(3).strip()
    mult = re.match(r"^[x×]\s+(.+)$", rest, re.I)
    if mult:
        name, inner, unit, orig = split_quantity(mult.group(1).strip(), extra_units)
        return name, q * (1 if inner is None else inner), unit, orig
    first, _, after = rest.partition(" ")
    u, after = first.lower().rstrip("."), after.strip()
    if after and (u in UNIT_ALIASES or u in extra_units):
        if after.lower().startswith("of "):
            after = after[3:].strip()
        if u in UNIT_ALIASES:
            unit, factor = UNIT_ALIASES[u]
            return after, q * factor, unit, SYMBOL.get(u, unit)
        return after, q, u, u
    return rest, q, "", ""


def parse_ingredient_full(line, extra_units=(), synonyms=None, system="recipe"):
    """-> (key, display name, quantity, unit, recipe symbol).
    '500 g minced beef' -> ('minced beef', 'Minced beef', 500.0, 'g', 'g');
    '1 large red onion, finely chopped' -> ('onion', 'Onion', 1.0, '', '') (built-in synonym).
    Text in brackets and notes after a comma are ignored, so recipe-site lines merge with plain ones.
    With system 'metric' or 'imperial' ounces, pounds and cups are converted to grams / millilitres."""
    full = clean_ingredient(line)
    short = re.sub(r"\s+", " ", NOTE_COMMA.split(re.sub(r"\([^)]*\)", " ", full), 1)[0]).strip()
    name, qty, unit, sym = split_quantity(short or full, extra_units)
    name = name.strip()
    trimmed = SIZE_WORDS.sub("", name)
    if trimmed:
        name = trimmed
    if qty is not None and system != "recipe" and unit in TO_BASE:
        unit, factor = TO_BASE[unit]
        qty, sym = qty * factor, ""
    key = singular(name)
    canon = canon_key(key, synonyms if synonyms is not None else BUILTIN_SYNONYMS)
    if canon != key:  # a synonym fired: show the common word
        key, name = canon, canon
    return key, name[:1].upper() + name[1:], qty, unit, sym


def parse_ingredient(line, extra_units=(), synonyms=None, system="recipe"):
    """Like parse_ingredient_full without the recipe symbol: (key, name, quantity, unit)."""
    return parse_ingredient_full(line, extra_units, synonyms, system)[:4]


# ---------------------------------------------------------------- display

def _num(q):
    return f"{round(q, 2):g}"


def fmt_qty(q, unit, system="recipe", symbol=""):
    """'1.5 kg', '2 dl', '4 cloves'. g/ml switch to kg/l from 1000. In the 'recipe' system dl and cl stay dl and cl
    when the recipes used them (symbol); 'metric' and 'imperial' convert to that system."""
    if unit == "g":
        if system == "imperial":
            return f"{_num(q / LB_G)} lb" if q >= 16 * OZ_G else f"{_num(q / OZ_G)} oz"
        return f"{_num(q / 1000)} kg" if q >= 1000 else f"{_num(q)} g"
    if unit == "ml":
        if system == "imperial":
            if q >= CUP_ML / 4:
                cups = round(q / CUP_ML, 2)
                return f"{_num(cups)} cup" + ("" if cups == 1 else "s")
            return f"{_num(q / FLOZ_ML)} fl oz"
        if system == "recipe" and symbol in ("cl", "dl") and SYMBOL_FACTOR[symbol] <= q < 1000:
            return f"{_num(q / SYMBOL_FACTOR[symbol])} {symbol}"  # the recipes wrote dl / cl: keep it
        return f"{_num(q / 1000)} l" if q >= 1000 else f"{_num(q)} ml"
    q = round(q, 2)
    if q != 1 and unit in PLURAL_UNITS:
        unit = PLURAL_UNITS[unit]
    return f"{_num(q)} {unit}" if unit else _num(q)
