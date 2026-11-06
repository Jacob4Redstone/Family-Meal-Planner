"""Family meal planner: meals -> automatic planning -> leftovers -> weekly shopping lists."""
import hmac
import html as htmllib
import ipaddress
import json
import os
import random
import re
import socket
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from flask import Flask, Response, abort, g, jsonify, request
from werkzeug.exceptions import HTTPException

from ingredients import build_synonyms, clean_ingredient, fmt_qty, parse_ingredient, parse_ingredient_full, singular  # noqa: F401

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
GAP_DAYS = int(os.environ.get("GAP_DAYS", "21"))  # a meal may not repeat within this many days
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")  # optional HTTP basic-auth password (any user name)
ENABLE_URL_IMPORT = os.environ.get("ENABLE_URL_IMPORT", "").lower() in ("1", "true", "yes")
SPILL = 3  # a meal can make leftovers for at most this many following days
UNDO_SECONDS = 3600  # "Undo last planning" is offered for this long (and only until the plan changes otherwise)
SHOPPING_KEEP_DAYS = 90  # ticks and extra items of older weeks are deleted

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_BODY_BYTES", str(5 * 1024 * 1024)))
app.config["DB_PATH"] = os.path.join(DATA_DIR, "mealplanner.db")

# ---------------------------------------------------------------- database

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  leftovers INTEGER NOT NULL DEFAULT 0,
  ingredients TEXT NOT NULL DEFAULT ''          -- one ingredient per line
);
CREATE UNIQUE INDEX IF NOT EXISTS meals_name ON meals(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS plan(
  day TEXT PRIMARY KEY,                         -- YYYY-MM-DD
  kind TEXT NOT NULL,                           -- meal | leftover | custom
  meal_id INTEGER,                              -- the meal (or the meal the leftovers come from)
  label TEXT NOT NULL DEFAULT ''                -- name snapshot / free text
);
"""

# (table, column, ddl) added by schema version 2
V2_COLUMNS = [
    ("meals", "weight", "INTEGER NOT NULL DEFAULT 1"),          # 0 rarely, 1 normal, 2 favourite
    ("meals", "weekdays", "TEXT NOT NULL DEFAULT ''"),          # allowed weekdays, '0,1,4' (Mon=0); empty = any
    ("meals", "leftover_days", "INTEGER NOT NULL DEFAULT 1"),   # how many days the leftovers last
    ("meals", "servings", "INTEGER NOT NULL DEFAULT 0"),        # servings the ingredient list makes; 0 = unscaled
    ("meals", "prep_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("meals", "url", "TEXT NOT NULL DEFAULT ''"),
    ("meals", "notes", "TEXT NOT NULL DEFAULT ''"),
    ("plan", "locked", "INTEGER NOT NULL DEFAULT 0"),           # pinned: planning never touches it
    ("plan", "ing_snapshot", "TEXT NOT NULL DEFAULT ''"),       # ingredients kept when the meal is deleted
]
V2_STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS checked(scope TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(scope, key))",
    "CREATE TABLE IF NOT EXISTS extras(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, name TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS plan_meal ON plan(meal_id)",
]
# schema version 3: rules, lunch, cooks, shopping extras
V3_COLUMNS = [
    ("meals", "tags", "TEXT NOT NULL DEFAULT ''"),               # 'quick,vegetarian'
    ("meals", "months", "TEXT NOT NULL DEFAULT ''"),             # months it suits, '6,7,8'; empty = all year
    ("meals", "prep_ahead", "TEXT NOT NULL DEFAULT ''"),         # 'Marinate the chicken'
    ("meals", "prep_ahead_days", "INTEGER NOT NULL DEFAULT 1"),  # shown this many days before
    ("plan", "cook", "TEXT NOT NULL DEFAULT ''"),                # per-day cook (otherwise the weekday rule)
    ("checked", "price", "REAL"),                                # price paid for a ticked item
]
V3_STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS plan_lunch(day TEXT PRIMARY KEY, kind TEXT NOT NULL, meal_id INTEGER, "
    "label TEXT NOT NULL DEFAULT '', ing_snapshot TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS aisle_map(key TEXT PRIMARY KEY, aisle TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS item_price(key TEXT PRIMARY KEY, price REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS stock(key TEXT PRIMARY KEY, until TEXT NOT NULL)",
]
SCHEMA_VERSION = 3


def init_db():
    """Create or migrate the database (PRAGMA user_version tracks the schema)."""
    path = app.config["DB_PATH"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(SCHEMA_V1)
        if version < 2:
            for table, col, ddl in V2_COLUMNS:
                have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            for stmt in V2_STATEMENTS:
                conn.execute(stmt)
        if version < 3:
            for table, col, ddl in V3_COLUMNS:
                have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            for stmt in V3_STATEMENTS:
                conn.execute(stmt)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        prune_shopping(conn)
        conn.commit()
    finally:
        conn.close()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DB_PATH"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


@app.teardown_appcontext
def _close(_exc):
    conn = g.pop("db", None)
    if conn:
        conn.close()


@app.errorhandler(HTTPException)
def _err(e):
    return jsonify(error=e.description), e.code


@app.before_request
def _guard():
    if request.path == "/healthz":
        return None
    if AUTH_PASSWORD:
        a = request.authorization
        if not (a and hmac.compare_digest((a.password or "").encode(), AUTH_PASSWORD.encode())):
            return Response("Authentication required.", 401, {"WWW-Authenticate": 'Basic realm="Meal planner"'})
    # A custom header forces a CORS preflight, so other websites cannot change data via the browser.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "fetch":
        abort(403, "Missing X-Requested-With header.")
    return None


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


def to_int(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


# ---------------------------------------------------------------- settings

UNIT_SYSTEMS = ("recipe", "metric", "imperial")
DEFAULT_SETTINGS = {
    "household": 0, "week_start": 0, "pantry": [], "units": [], "slots": [], "quotas": [],
    "synonyms": [],             # 'red onion = onion'
    "unit_system": "recipe",    # recipe = use the unit the recipes used; metric; imperial
    "avoid": [],                # words that exclude a meal from planning (allergies, dislikes)
    "cooks": [],                # 'Mon-Fri = Anna'
    "rules": [],                # 'Mon-Fri = quick' (prefer meals with that tag on those weekdays)
    "aisle_order": [],          # order of the shopping list groups
}
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_ALIASES = {"man": 0, "tir": 1, "ons": 2, "tor": 3, "fre": 4, "lør": 5, "søn": 6}


def weekday_index(tok):
    tok = tok.strip().lower()
    if tok.isdigit() and 0 <= int(tok) <= 6:
        return int(tok)
    if tok[:3] in DAY_NAMES:
        return DAY_NAMES.index(tok[:3])
    return DAY_ALIASES.get(tok[:3])


def parse_weekdays(x):
    """['mon','Tue'] / 'Mon, Tue' / '0,1' / [0, 1] -> sorted list of weekday numbers (Mon=0)."""
    if isinstance(x, str):
        x = re.split(r"[,\s;]+", x)
    out = set()
    for t in x or []:
        i = weekday_index(str(t)) if str(t).strip() else None
        if i is not None:
            out.add(i)
    return sorted(out)


MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_ALIASES = {"maj": 5, "okt": 10}


def month_index(tok):
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok) if 1 <= int(tok) <= 12 else None
    if tok[:3] in MONTH_NAMES:
        return MONTH_NAMES.index(tok[:3]) + 1
    return MONTH_ALIASES.get(tok[:3])


def parse_months(x):
    """'Jun-Aug' / 'Dec-Feb' (wraps) / '3,4,5' / [6, 7] -> sorted list of month numbers 1..12."""
    if isinstance(x, str):
        x = re.split(r"[,;\s]+", re.sub(r"\s*[-–]\s*", "-", x.strip()))
    out = set()
    for t in x or []:
        t = str(t).strip()
        if not t:
            continue
        if "-" in t:
            lo, _, hi = t.partition("-")
            a, b = month_index(lo), month_index(hi)
            if a is None or b is None:
                continue
            i = a
            while True:
                out.add(i)
                if i == b:
                    break
                i = i % 12 + 1
        elif month_index(t) is not None:
            out.add(month_index(t))
    return sorted(out)


def parse_tags(x):
    if isinstance(x, (list, tuple)):
        x = ",".join(str(t) for t in x)
    return sorted({re.sub(r"\s+", " ", t).strip().lower()[:30] for t in re.split(r"[,;\n]+", x or "")} - {""})[:20]


def parse_day_set(text):
    """'Mon-Fri' / 'Sat, Sun' / 'Fri' -> set of weekday numbers (Mon=0); ranges may wrap ('Fri-Mon')."""
    out = set()
    for part in re.split(r"[,;&]+", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([^-–]+?)\s*[-–]\s*(.+)$", part)
        if m:
            a, b = weekday_index(m.group(1)), weekday_index(m.group(2))
            if a is None or b is None:
                raise ValueError(part)
            i = a
            while True:
                out.add(i)
                if i == b:
                    break
                i = (i + 1) % 7
        else:
            i = weekday_index(part)
            if i is None:
                raise ValueError(part)
            out.add(i)
    if not out:
        raise ValueError(text)
    return out


def parse_cooks(lines):
    """'Mon-Fri = Anna' -> {weekday: name}"""
    out = {}
    for l in lines:
        m = re.match(r"^\s*([^=:]+?)\s*[=:]\s*(.+?)\s*$", l)
        try:
            days = parse_day_set(m.group(1)) if m else None
        except ValueError:
            days = None
        if not days:
            raise ValueError(f"Cannot read the cook line “{l}”. Use for example: Mon-Fri = Anna")
        for i in days:
            out[i] = m.group(2)[:40]
    return out


def parse_rules(lines):
    """'Mon-Fri = quick' -> [(set of weekdays, 'quick'), ...]"""
    out = []
    for l in lines:
        m = re.match(r"^\s*([^=:]+?)\s*[=:]\s*(.+?)\s*$", l)
        try:
            days = parse_day_set(m.group(1)) if m else None
        except ValueError:
            days = None
        if not days:
            raise ValueError(f"Cannot read the tag rule “{l}”. Use for example: Mon-Fri = quick")
        out += [(days, t.strip().lower()) for t in m.group(2).split(",") if t.strip()]
    return out


def parse_slots(lines):
    out = {}
    for l in lines:
        m = re.match(r"^\s*([^=:]+?)\s*[=:]\s*(.+?)\s*$", l)
        i = weekday_index(m.group(1)) if m else None
        if i is None:
            raise ValueError(f"Cannot read the fixed-day line “{l}”. Use for example: Friday = Takeaway")
        out[i] = m.group(2)[:80]
    return out


def parse_quotas(lines):
    out = {}
    for l in lines:
        m = re.match(r"^\s*(.+?)\s*[=:]\s*(\d+)\s*$", l)
        if not m:
            raise ValueError(f"Cannot read the category limit “{l}”. Use for example: Pasta = 2")
        out[m.group(1).lower()] = int(m.group(2))
    return out


def lines_of(v):
    if isinstance(v, str):
        v = v.splitlines()
    return [re.sub(r"\s+", " ", str(x)).strip() for x in (v or []) if str(x).strip()]


def clean_settings(d):
    s = {
        "household": to_int(d.get("household"), 0, 50, 0),
        "week_start": 6 if to_int(d.get("week_start"), 0, 6, 0) == 6 else 0,
        "pantry": lines_of(d.get("pantry"))[:200],
        "units": [u.lower().rstrip(".") for u in re.split(r"[,\s]+", " ".join(lines_of(d.get("units")))) if u][:50],
        "slots": lines_of(d.get("slots"))[:14],
        "quotas": lines_of(d.get("quotas"))[:50],
        "synonyms": lines_of(d.get("synonyms"))[:200],
        "unit_system": d.get("unit_system") if d.get("unit_system") in UNIT_SYSTEMS else "recipe",
        "avoid": [t.lower() for t in lines_of(d.get("avoid"))][:100],
        "cooks": lines_of(d.get("cooks"))[:14],
        "rules": lines_of(d.get("rules"))[:30],
        "aisle_order": lines_of(d.get("aisle_order"))[:30],
    }
    try:
        parse_slots(s["slots"])
        parse_quotas(s["quotas"])
        parse_cooks(s["cooks"])
        parse_rules(s["rules"])
        build_synonyms(s["synonyms"])
    except ValueError as e:
        abort(400, str(e))
    return s


def get_settings(c):
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    row = c.execute("SELECT value FROM settings WHERE key='main'").fetchone()
    if row:
        s.update(json.loads(row["value"]))
    return s


def save_settings(c, s):
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('main', ?)", (json.dumps(s),))


@app.get("/api/config")
def config():
    return jsonify(gap_days=GAP_DAYS, url_import=ENABLE_URL_IMPORT, auth=bool(AUTH_PASSWORD), spill=SPILL)


@app.get("/api/settings")
def settings_get():
    return jsonify(get_settings(db()))


@app.put("/api/settings")
def settings_put():
    c = db()
    old = get_settings(c)
    # Keys that are left out keep their stored value.
    s = clean_settings({**old, **(request.get_json(silent=True) or {})})
    if s["week_start"] != old["week_start"]:
        rekey_shopping(c, s["week_start"])
    save_settings(c, s)
    c.commit()
    return jsonify(s)


def week_start_of(d, ws):
    """First day of the week containing d (ws: 0 = Monday, 6 = Sunday)."""
    return d - timedelta((d.weekday() - ws) % 7)


def client_today():
    """The user's own 'today' (the browser sends it), because the server may run in another time zone."""
    v = request.args.get("today")
    if v is None:
        body = request.get_json(silent=True)
        v = body.get("today") if isinstance(body, dict) else None
    return parse_day(v) if v else date.today()


def drop_undo(c):
    """Any plan change other than planning itself makes the stored undo snapshot stale."""
    c.execute("DELETE FROM settings WHERE key='undo'")


def prune_shopping(c):
    """Delete ticks and extra items of weeks that are long over (scope = 'YYYY-MM-DD:weeks')."""
    cutoff = (date.today() - timedelta(SHOPPING_KEEP_DAYS)).isoformat()
    c.execute("DELETE FROM extras WHERE substr(scope, 1, 10) < ?", (cutoff,))
    c.execute("DELETE FROM checked WHERE substr(scope, 1, 10) < ?", (cutoff,))
    c.execute("DELETE FROM checked WHERE key LIKE 'extra:%' AND CAST(substr(key, 7) AS INTEGER) NOT IN (SELECT id FROM extras)")


def rekey_shopping(c, ws):
    """Week boundaries moved: extra items (and their ticks) follow the week that now contains them;
    ingredient ticks are dropped, because they no longer describe the same days."""
    c.execute("DELETE FROM checked WHERE key NOT LIKE 'extra:%'")
    for r in c.execute("SELECT DISTINCT scope FROM extras").fetchall():
        start, _, weeks = r["scope"].partition(":")
        try:
            new = f"{week_start_of(date.fromisoformat(start), ws).isoformat()}:{weeks}"
        except ValueError:
            continue
        if new != r["scope"]:
            c.execute("UPDATE extras SET scope=? WHERE scope=?", (new, r["scope"]))
            c.execute("UPDATE OR IGNORE checked SET scope=? WHERE scope=? AND key LIKE 'extra:%'", (new, r["scope"]))


# ---------------------------------------------------------------- meals

COLS = ("name", "category", "leftovers", "ingredients", "weight", "weekdays", "leftover_days",
        "servings", "prep_minutes", "url", "notes", "tags", "months", "prep_ahead", "prep_ahead_days")


def span_of(r):
    """Number of leftover days a meal row makes."""
    return max(1, min(SPILL, r["leftover_days"])) if r["leftovers"] else 0


def meal_avoided(m, avoid):
    """The first avoid-list term that this meal matches (a tag, or a whole word in the name or ingredients), else ''."""
    if not avoid:
        return ""
    tags = {t for t in m["tags"].split(",") if t}
    text = (m["name"] + "\n" + m["ingredients"]).lower()
    for term in avoid:
        if term in tags or re.search(r"\b" + re.escape(term) + r"(?:e?s)?\b", text):
            return term
    return ""


def meal_dict(r, avoid=()):
    keys = r.keys()
    d = {
        "id": r["id"],
        "name": r["name"],
        "category": r["category"],
        "leftovers": bool(r["leftovers"]),
        "leftover_days": max(1, min(SPILL, r["leftover_days"])),
        "ingredients": [i for i in r["ingredients"].split("\n") if i],
        "weight": r["weight"],
        "weekdays": [int(x) for x in r["weekdays"].split(",") if x.strip().isdigit()],
        "servings": r["servings"],
        "prep_minutes": r["prep_minutes"],
        "url": r["url"],
        "notes": r["notes"],
        "tags": [t for t in r["tags"].split(",") if t],
        "months": [int(x) for x in r["months"].split(",") if x.strip().isdigit()],
        "prep_ahead": r["prep_ahead"],
        "prep_ahead_days": max(1, min(3, r["prep_ahead_days"])),
        "avoided": meal_avoided(r, avoid),
    }
    if "last_served" in keys:
        d["last_served"] = r["last_served"]
        d["times_served"] = r["times_served"]
    return d


def clean_meal(d):
    name = re.sub(r"\s+", " ", d.get("name") or "").strip()
    if not name:
        abort(400, "A meal needs a name.")
    ing = d.get("ingredients") or []
    if isinstance(ing, str):
        ing = ing.split("\n")
    ing = [i for i in (clean_ingredient(x) for x in ing) if i]
    url = (d.get("url") or "").strip()[:500]
    if url and not re.match(r"https?://", url, re.I):
        abort(400, "The link must start with http:// or https://")
    return {
        "name": name[:120],
        "category": (d.get("category") or "").strip()[:60],
        "leftovers": int(bool(d.get("leftovers"))),
        "ingredients": "\n".join(ing),
        "weight": to_int(d.get("weight"), 0, 2, 1),
        "weekdays": ",".join(str(i) for i in parse_weekdays(d.get("weekdays"))),
        "leftover_days": to_int(d.get("leftover_days"), 1, SPILL, 1),
        "servings": to_int(d.get("servings"), 0, 99, 0),
        "prep_minutes": to_int(d.get("prep_minutes"), 0, 1440, 0),
        "url": url,
        "notes": (d.get("notes") or "").strip()[:2000],
        "tags": ",".join(parse_tags(d.get("tags"))),
        "months": ",".join(str(i) for i in parse_months(d.get("months"))),
        "prep_ahead": re.sub(r"\s+", " ", d.get("prep_ahead") or "").strip()[:200],
        "prep_ahead_days": to_int(d.get("prep_ahead_days"), 1, 3, 1),
    }


def insert_meal(c, v, mid=None):
    cols = COLS + (("id",) if mid is not None else ())
    vals = [v[k] for k in COLS] + ([mid] if mid is not None else [])
    cur = c.execute(f"INSERT INTO meals({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    return cur.lastrowid


# every served meal, dinner and lunch (used for "last served" and statistics)
SERVED_SQL = ("SELECT day, meal_id FROM plan WHERE kind='meal' "
              "UNION ALL SELECT day, meal_id FROM plan_lunch WHERE kind='meal'")


def select_meals(c, today):
    today = today.isoformat()
    return c.execute(
        "SELECT m.*, "
        f"(SELECT MAX(day) FROM ({SERVED_SQL}) s WHERE s.meal_id=m.id AND s.day<=?) AS last_served, "
        f"(SELECT COUNT(*) FROM ({SERVED_SQL}) s WHERE s.meal_id=m.id AND s.day<=?) AS times_served "
        "FROM meals m ORDER BY m.name COLLATE NOCASE", (today, today)).fetchall()


@app.get("/api/meals")
def list_meals():
    c = db()
    avoid = get_settings(c)["avoid"]
    return jsonify([meal_dict(r, avoid) for r in select_meals(c, client_today())])


@app.post("/api/meals")
def add_meal():
    v = clean_meal(request.get_json(silent=True) or {})
    try:
        mid = insert_meal(db(), v)
        db().commit()
    except sqlite3.IntegrityError:
        abort(409, "A meal with this name already exists.")
    return jsonify(id=mid), 201


@app.put("/api/meals/<int:mid>")
def edit_meal(mid):
    v = clean_meal(request.get_json(silent=True) or {})
    today = client_today()
    c = db()
    try:
        cur = c.execute(f"UPDATE meals SET {','.join(k + '=?' for k in COLS)} WHERE id=?", [v[k] for k in COLS] + [mid])
    except sqlite3.IntegrityError:
        abort(409, "A meal with this name already exists.")
    if cur.rowcount == 0:
        abort(404, "Meal not found.")
    # Keep the plan consistent: new name on the calendar; leftover days follow the new setting,
    # but only from today on (days that are over stay as they were).
    c.execute("UPDATE plan SET label=? WHERE meal_id=?", (v["name"], mid))
    c.execute("UPDATE plan_lunch SET label=? WHERE meal_id=?", (v["name"], mid))
    span = span_of({"leftovers": v["leftovers"], "leftover_days": v["leftover_days"]})
    skipped, before = [], c.total_changes
    for r in c.execute("SELECT day FROM plan WHERE meal_id=? AND kind='meal' AND day>=?",
                       (mid, today.isoformat())).fetchall():
        skipped += sync_leftovers(c, date.fromisoformat(r["day"]), mid, span, v["name"])
    if c.total_changes != before:
        drop_undo(c)
    c.commit()
    return jsonify(ok=True, skipped=skipped)


@app.delete("/api/meals/<int:mid>")
def delete_meal(mid):
    c = db()
    m = c.execute("SELECT ingredients FROM meals WHERE id=?", (mid,)).fetchone()
    if m:  # planned days keep the name and the ingredients, so the calendar and shopping list stay intact
        c.execute("UPDATE plan SET ing_snapshot=? WHERE meal_id=? AND kind='meal'", (m["ingredients"], mid))
        c.execute("UPDATE plan_lunch SET ing_snapshot=? WHERE meal_id=? AND kind='meal'", (m["ingredients"], mid))
    c.execute("DELETE FROM meals WHERE id=?", (mid,))
    drop_undo(c)  # restoring an older snapshot would bring back plan rows without the ingredient snapshot
    c.commit()
    return jsonify(ok=True)


def truthy(s):
    return str(s).strip().lower() in ("yes", "y", "true", "1", "x")


def split_ingredients(s):
    """Split on commas, but keep decimal commas such as '1,5 dl' together."""
    return [x for x in re.split(r"(?<!\d),|,(?!\d)|;", s) if x.strip()]


WEIGHT_WORDS = {"rarely": 0, "rare": 0, "normal": 1, "favourite": 2, "favorite": 2}
EXTRA_KEYS = {"servings": "servings", "prep": "prep_minutes", "prep time": "prep_minutes", "url": "url",
              "link": "url", "notes": "notes", "weekdays": "weekdays", "leftover days": "leftover_days",
              "weight": "weight", "tags": "tags", "months": "months", "prep ahead": "prep_ahead",
              "prep ahead days": "prep_ahead_days"}


def parse_import(text):
    """Blocks separated by blank lines. Either
         Name | Category | yes        followed by '- ingredient' lines
       or
         Name / Category: .. / Leftovers: .. / Ingredients: (list or comma/semicolon separated)
       Optional extra lines: Servings:, Prep:, URL:, Notes:, Weekdays:, Leftover days:, Weight:, Tags:, Months:,
       Prep ahead:, Prep ahead days:"""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        name, cat, lo, ings, extra = lines[0], "", False, [], {}
        if "|" in name:
            p = [x.strip() for x in name.split("|")]
            name, cat, lo = p[0], (p[1] if len(p) > 1 else ""), (len(p) > 2 and truthy(p[2]))
        for l in lines[1:]:
            low = l.lower()
            head = low.split(":", 1)[0] if ":" in low else ""
            if low.startswith("category:"):
                cat = l.split(":", 1)[1].strip()
            elif low.startswith("leftovers:"):
                lo = truthy(l.split(":", 1)[1])
            elif low.startswith("ingredients:"):
                ings += split_ingredients(l.split(":", 1)[1])
            elif head in EXTRA_KEYS:
                val = l.split(":", 1)[1].strip()
                if head == "weight":
                    val = WEIGHT_WORDS.get(val.lower(), val)
                extra[EXTRA_KEYS[head]] = val
            else:
                ings.append(l)
        if name:
            out.append({"name": name, "category": cat, "leftovers": lo, "ingredients": ings, **extra})
    return out


@app.get("/api/meals/export")
def export_meals():
    rows = db().execute("SELECT * FROM meals ORDER BY name COLLATE NOCASE").fetchall()
    blocks = []
    for r in rows:
        head = f"{r['name']} | {r['category']} | {'yes' if r['leftovers'] else 'no'}"
        blocks.append("\n".join([head] + [f"- {i}" for i in r["ingredients"].split("\n") if i]))
    return Response("\n\n".join(blocks) + "\n", mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=meals.txt"})


@app.post("/api/meals/import")
def import_meals():
    update = request.args.get("update") in ("1", "true")
    c = db()
    added = skipped = updated = 0
    for m in parse_import(request.get_data(as_text=True)):
        v = clean_meal(m)
        try:
            insert_meal(c, v)
            added += 1
        except sqlite3.IntegrityError:
            if not update:
                skipped += 1
                continue
            keys = {"category", "leftovers"} | ({"ingredients"} if v["ingredients"] else set())
            keys |= {k for k in m if k in COLS and k not in ("name", "category", "leftovers", "ingredients")}
            if "leftovers" in keys and v["leftovers"]:
                keys.add("leftover_days")
            ks = sorted(keys)
            c.execute(f"UPDATE meals SET {','.join(k + '=?' for k in ks)} WHERE name=? COLLATE NOCASE",
                      [v[k] for k in ks] + [v["name"]])
            updated += 1
    c.commit()
    return jsonify(added=added, skipped=skipped, updated=updated)


# ---------------------------------------------------------------- recipe from URL (opt-in)

def _validate_public_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Only http:// and https:// links work.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError:
        raise ValueError("The address could not be found.") from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Links to private or local addresses are not allowed.")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url):
    _validate_public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "MealPlanner/2.0", "Accept": "text/html,*/*"})
    try:
        with urllib.request.build_opener(_SafeRedirect).open(req, timeout=8) as r:
            data = r.read(1_000_001)
    except ValueError:
        raise
    except Exception as e:  # network errors, HTTP errors, timeouts
        raise ValueError(f"The page could not be loaded ({type(e).__name__}).") from None
    if len(data) > 1_000_000:
        raise ValueError("The page is too large.")
    return data.decode("utf-8", "replace")


def _nodes(x):
    if isinstance(x, list):
        for i in x:
            yield from _nodes(i)
    elif isinstance(x, dict):
        yield x
        yield from _nodes(x.get("@graph"))


def parse_recipe(page, url=""):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for n in _nodes(data):
            types = n.get("@type")
            if "Recipe" not in (types if isinstance(types, list) else [types]):
                continue
            cat = n.get("recipeCategory") or ""
            if isinstance(cat, list):
                cat = cat[0] if cat else ""
            yld = n.get("recipeYield")
            yld = yld[0] if isinstance(yld, list) and yld else yld
            ym = re.search(r"\d+", str(yld or ""))
            dur = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", str(n.get("totalTime") or n.get("prepTime") or ""))
            minutes = int(dur.group(1) or 0) * 60 + int(dur.group(2) or 0) if dur else 0
            ings = [htmllib.unescape(str(i)) for i in (n.get("recipeIngredient") or [])]
            return {"name": htmllib.unescape(str(n.get("name") or "")), "category": htmllib.unescape(str(cat)),
                    "ingredients": ings, "servings": int(ym.group()) if ym else 0, "prep_minutes": minutes,
                    "url": url}
    return None


@app.post("/api/meals/from-url")
def meal_from_url():
    if not ENABLE_URL_IMPORT:
        abort(404, "Importing from a link is switched off (set ENABLE_URL_IMPORT=1).")
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    try:
        recipe = parse_recipe(fetch_page(url), url)
    except ValueError as e:
        abort(400, str(e))
    if not recipe:
        abort(422, "No recipe data was found on that page.")
    return jsonify(recipe)


# ---------------------------------------------------------------- plan

def parse_day(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        abort(400, "Invalid date.")


def save_entry(c, d, kind, meal_id, label, locked=0, cook=None):
    """Write a plan row. The per-day cook is kept from the row being replaced unless `cook` is given."""
    c.execute("INSERT OR REPLACE INTO plan(day,kind,meal_id,label,locked,ing_snapshot,cook) "
              "VALUES (?,?,?,?,?,'',COALESCE(?, (SELECT cook FROM plan WHERE day=?), ''))",
              (d.isoformat(), kind, meal_id, label, int(bool(locked)), cook, d.isoformat()))


def sync_leftovers(c, d, meal_id, span, name):
    """Make the days after a meal on day d match its leftover span. Occupied days are never overwritten;
    returns the days that could not be reserved."""
    skipped = []
    for j in range(1, SPILL + 1):
        dd = d + timedelta(j)
        r = c.execute("SELECT kind, meal_id, locked FROM plan WHERE day=?", (dd.isoformat(),)).fetchone()
        same = bool(r and r["kind"] == "leftover" and r["meal_id"] == meal_id)
        if j <= span:
            if r is None:
                save_entry(c, dd, "leftover", meal_id, name)
            elif not same:
                skipped.append(dd.isoformat())
                break
        elif same and not r["locked"]:
            c.execute("DELETE FROM plan WHERE day=?", (dd.isoformat(),))
        else:
            break
    return skipped


@app.get("/api/plan")
def get_plan():
    start, end = parse_day(request.args.get("start")), parse_day(request.args.get("end"))
    if (end - start).days > 400:
        abort(400, "That range is too long.")
    rows = db().execute(
        "SELECT p.day, p.kind, p.meal_id, p.locked, COALESCE(m.name, p.label) AS name, m.category AS category "
        "FROM plan p LEFT JOIN meals m ON m.id = p.meal_id WHERE p.day BETWEEN ? AND ? ORDER BY p.day",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return jsonify([{**dict(r), "locked": bool(r["locked"])} for r in rows])


@app.put("/api/plan/<day>")
def put_day(day):
    d = parse_day(day)
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    c = db()
    old = c.execute("SELECT kind, meal_id, locked FROM plan WHERE day=?", (d.isoformat(),)).fetchone()
    # Replacing a meal frees the leftover days it had reserved. Replacing or clearing a leftover day
    # frees the leftover days that follow it, so no orphaned tail is left behind.
    if old and old["kind"] == "meal":
        sync_leftovers(c, d, old["meal_id"], 0, "")
    elif old and old["kind"] == "leftover" and not (kind == "leftover" and body.get("meal_id") == old["meal_id"]):
        sync_leftovers(c, d, old["meal_id"], 0, "")
    locked = body.get("locked", bool(old and old["locked"]))
    cook = str(body["cook"]).strip()[:40] if "cook" in body else None  # None keeps the current one
    skipped = []
    if kind == "custom" and (body.get("label") or "").strip():
        save_entry(c, d, "custom", None, body["label"].strip()[:80], locked, cook)
    elif kind in ("meal", "leftover"):
        m = c.execute("SELECT * FROM meals WHERE id=?", (body.get("meal_id"),)).fetchone()
        if not m:
            abort(400, "Unknown meal.")
        save_entry(c, d, kind, m["id"], m["name"], locked, cook)
        if kind == "meal":
            skipped = sync_leftovers(c, d, m["id"], span_of(m), m["name"])
    else:  # empty the day
        c.execute("DELETE FROM plan WHERE day=?", (d.isoformat(),))
    drop_undo(c)
    c.commit()
    return jsonify(ok=True, skipped=skipped)


@app.post("/api/plan/swap")
def swap_days():
    body = request.get_json(silent=True) or {}
    a, b = parse_day(body.get("a")), parse_day(body.get("b"))
    c = db()
    rows = {}
    for d in (a, b):
        r = c.execute("SELECT kind, meal_id, label, locked, cook FROM plan WHERE day=?", (d.isoformat(),)).fetchone()
        if r and r["kind"] == "leftover":
            abort(400, "Leftover days move together with their meal. Move the meal instead.")
        rows[d] = r
    for d, r in rows.items():
        if r and r["kind"] == "meal":
            sync_leftovers(c, d, r["meal_id"], 0, "")
    skipped = []
    for d, other in ((a, b), (b, a)):
        r = rows[other]
        if r is None:
            c.execute("DELETE FROM plan WHERE day=?", (d.isoformat(),))
            continue
        save_entry(c, d, r["kind"], r["meal_id"], r["label"], r["locked"], r["cook"])
        if r["kind"] == "meal":
            m = c.execute("SELECT * FROM meals WHERE id=?", (r["meal_id"],)).fetchone()
            if m:
                skipped += sync_leftovers(c, d, m["id"], span_of(m), m["name"])
    drop_undo(c)
    c.commit()
    return jsonify(ok=True, skipped=skipped)


# ---------------------------------------------------------------- lunch, cooks, prep-ahead reminders

@app.put("/api/lunch/<day>")
def put_lunch(day):
    """The lunch slot is filled by hand (meal, leftovers or a note); automatic planning never touches it."""
    d = parse_day(day)
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    c = db()
    if kind == "custom" and (body.get("label") or "").strip():
        c.execute("INSERT OR REPLACE INTO plan_lunch(day,kind,meal_id,label,ing_snapshot) VALUES (?,'custom',NULL,?,'')",
                  (d.isoformat(), body["label"].strip()[:80]))
    elif kind in ("meal", "leftover"):
        m = c.execute("SELECT id, name FROM meals WHERE id=?", (body.get("meal_id"),)).fetchone()
        if not m:
            abort(400, "Unknown meal.")
        c.execute("INSERT OR REPLACE INTO plan_lunch(day,kind,meal_id,label,ing_snapshot) VALUES (?,?,?,?,'')",
                  (d.isoformat(), kind, m["id"], m["name"]))
    else:
        c.execute("DELETE FROM plan_lunch WHERE day=?", (d.isoformat(),))
    c.commit()
    return jsonify(ok=True)


def lunch_map(c, start, end):
    rows = c.execute(
        "SELECT l.day, l.kind, l.meal_id, COALESCE(m.name, l.label) AS name FROM plan_lunch l "
        "LEFT JOIN meals m ON m.id = l.meal_id WHERE l.day BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
    return {r["day"]: dict(r) for r in rows}


def cook_overrides(c, start, end):
    """{day: name} for days where a cook was chosen by hand."""
    return {r["day"]: r["cook"] for r in c.execute(
        "SELECT day, cook FROM plan WHERE cook != '' AND day BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))}


def cook_map(c, start, end, S):
    """{day: name}: the weekday rule from Settings, overridden by a cook chosen for that day."""
    rules = parse_cooks(S["cooks"])
    out, d = {}, start
    while d <= end:
        if d.weekday() in rules:
            out[d.isoformat()] = rules[d.weekday()]
        d += timedelta(1)
    out.update(cook_overrides(c, start, end))
    return out


def prep_reminders(c, start, end):
    """{day: [{text, meal, for_day, slot}]}: a meal's 'prepare ahead' note, shown the chosen number of days before."""
    out = {}
    for slot, table in (("dinner", "plan"), ("lunch", "plan_lunch")):
        rows = c.execute(
            f"SELECT p.day, m.name, m.prep_ahead, m.prep_ahead_days FROM {table} p JOIN meals m ON m.id = p.meal_id "
            "WHERE p.kind='meal' AND m.prep_ahead != '' AND p.day BETWEEN ? AND ?",
            ((start + timedelta(1)).isoformat(), (end + timedelta(SPILL)).isoformat()))
        for r in rows:
            for_day = date.fromisoformat(r["day"])
            due = for_day - timedelta(max(1, min(3, r["prep_ahead_days"])))
            if start <= due <= end:
                out.setdefault(due.isoformat(), []).append(
                    {"text": r["prep_ahead"], "meal": r["name"], "for_day": r["day"], "slot": slot})
    return out


@app.get("/api/plan/extras")
def plan_extras():
    """What the calendar shows besides the dinner plan: lunches, cooks and prepare-ahead reminders."""
    start, end = parse_day(request.args.get("start")), parse_day(request.args.get("end"))
    if (end - start).days > 400:
        abort(400, "That range is too long.")
    c = db()
    return jsonify(lunch=lunch_map(c, start, end), cook=cook_map(c, start, end, get_settings(c)),
                   override=cook_overrides(c, start, end), prep=prep_reminders(c, start, end))


# ---------------------------------------------------------------- planning

def load_planning_meals(c, settings):
    syn = build_synonyms(settings.get("synonyms", []))
    pantry = {parse_ingredient(p, (), syn)[0] for p in settings["pantry"]}
    meals = {}
    avoid = settings.get("avoid", [])
    for r in c.execute("SELECT * FROM meals"):
        if meal_avoided(r, avoid):
            continue  # hard rule: allergies and dislikes are never planned or suggested
        m = dict(r)
        m["tags"] = {t for t in r["tags"].split(",") if t}
        m["months"] = {int(x) for x in r["months"].split(",") if x.strip().isdigit()}
        m["weekdays"] = {int(x) for x in r["weekdays"].split(",") if x.strip().isdigit()}
        m["span"] = span_of(r)
        m["ingkeys"] = {parse_ingredient(l, (), syn)[0] for l in r["ingredients"].split("\n") if l} - pantry
        meals[r["id"]] = m
    return meals


def pick(d, cur, meals, S, exclude=()):
    """Choose a meal for day d. Returns (meal, was_forced_repeat)."""
    ws = S["week_start"]
    quotas = parse_quotas(S["quotas"])
    wk = week_start_of(d, ws)
    used, near, week_cat, week_keys = {}, [], {}, set()
    for k, e in cur.items():
        m = meals.get(e["meal_id"])
        if not m:
            continue
        gap = abs((k - d).days)
        cat = m["category"].lower()
        if e["kind"] == "meal":
            if gap < GAP_DAYS:
                used[m["id"]] = min(gap, used.get(m["id"], gap))
            if week_start_of(k, ws) == wk:
                week_cat[cat] = week_cat.get(cat, 0) + 1
                week_keys |= m["ingkeys"]
        if 0 < gap <= 2:
            near.append(cat)

    pool = [m for m in meals.values() if m["id"] not in used and m["id"] not in exclude]
    forced = 0
    if not pool:  # too few meals: reuse whichever was served longest ago
        forced = 1
        usable = [m for m in meals.values() if m["id"] not in exclude] or list(meals.values())
        far = max(used.get(m["id"], 10 ** 6) for m in usable)
        pool = [m for m in usable if used.get(m["id"], 10 ** 6) == far]

    def narrow(pool, ok):  # soft rule: only applied when something is left
        return [m for m in pool if ok(m)] or pool

    pool = narrow(pool, lambda m: not m["months"] or d.month in m["months"])  # in season
    for tag in sorted({t for days, t in parse_rules(S.get("rules", [])) if d.weekday() in days}):
        pool = narrow(pool, lambda m, tag=tag: tag in m["tags"])  # e.g. 'Mon-Fri = quick'
    pool = narrow(pool, lambda m: not m["weekdays"] or d.weekday() in m["weekdays"])
    pool = narrow(pool, lambda m: week_cat.get(m["category"].lower(), 0) < quotas.get(m["category"].lower(), 10 ** 6))
    if not forced:
        pool = narrow(pool, lambda m: not m["category"] or m["category"].lower() not in near)

    def weight(m):  # favourites more often; meals sharing ingredients with this week's meals waste less food
        base = {0: 0.3, 1: 1.0, 2: 2.0}.get(m["weight"], 1.0)
        return base * (1 + 0.5 * min(3, len(m["ingkeys"] & week_keys)))

    return random.choices(pool, weights=[weight(m) for m in pool])[0], forced


def leftover_source(orig, dd):
    """Day of the meal that the leftover on dd belongs to (looking back through leftover days)."""
    mid = orig[dd]["meal_id"]
    j = dd - timedelta(1)
    while True:
        p = orig.get(j)
        if not p or p["meal_id"] != mid:
            return None
        if p["kind"] == "meal":
            return j
        if p["kind"] != "leftover":
            return None
        j -= timedelta(1)


def snapshot_for_undo(c, lo, hi):
    rows = [dict(r) for r in c.execute("SELECT * FROM plan WHERE day BETWEEN ? AND ?", (lo.isoformat(), hi.isoformat()))]
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('undo', ?)",
              (json.dumps({"lo": lo.isoformat(), "hi": hi.isoformat(), "rows": rows, "t": time.time()}),))


def load_window(c, start, end):
    """The plan rows around start..end that the planning rules need to look at (gap days before and after)."""
    lo, hi = start - timedelta(GAP_DAYS + SPILL), end + timedelta(GAP_DAYS + 1 + SPILL)
    return {
        date.fromisoformat(r["day"]): dict(r)
        for r in c.execute("SELECT * FROM plan WHERE day BETWEEN ? AND ?", (lo.isoformat(), hi.isoformat()))
    }


def generate(start, end, replace, reroll=False):
    c = db()
    S = get_settings(c)
    meals = load_planning_meals(c, S)
    if not meals:
        if c.execute("SELECT 1 FROM meals").fetchone():
            abort(400, "Every meal is excluded by the avoid list in Settings.")
        abort(400, "Add some meals first.")
    slots = parse_slots(S["slots"])
    cur = load_window(c, start, end)
    orig = dict(cur)
    snapshot_for_undo(c, start - timedelta(SPILL), end + timedelta(SPILL))

    exclude, deleted, stale, skipped_locked = set(), set(), set(), 0
    if replace:
        for k in [k for k in cur if start <= k <= end]:
            if cur[k]["locked"]:
                skipped_locked += 1
                continue
            if reroll and cur[k]["meal_id"]:
                exclude.add(cur[k]["meal_id"])
            deleted.add(k)
            del cur[k]
        # Leftovers of a replaced meal that spill past the end of the range go too.
        dd = end + timedelta(1)
        while dd in orig and orig[dd]["kind"] == "leftover" and not orig[dd]["locked"]:
            if leftover_source(orig, dd) not in deleted:
                break
            stale.add(dd)
            cur.pop(dd, None)
            dd += timedelta(1)

    written = {}

    def reserve(k, e):  # leftover days after a meal on day k (only days from `start` on)
        m = meals.get(e["meal_id"])
        for j in range(1, (m["span"] if m else 0) + 1):
            dd = k + timedelta(j)
            if dd in cur:
                if cur[dd]["kind"] == "leftover" and cur[dd]["meal_id"] == m["id"]:
                    continue
                break
            if dd >= start:
                cur[dd] = written[dd] = {"kind": "leftover", "meal_id": m["id"], "label": m["name"], "locked": 0}

    if replace and not reroll:  # Replace deleted the range, so meals just before it owe their leftover days again
        for k in sorted(k for k in cur if start - timedelta(SPILL) <= k < start):
            if cur[k]["kind"] == "meal":
                reserve(k, cur[k])

    reused = 0
    d = start
    while d <= end:
        if d not in cur:
            if not reroll and d.weekday() in slots:
                e = {"kind": "custom", "meal_id": None, "label": slots[d.weekday()], "locked": 0}
            else:
                m, again = pick(d, cur, meals, S, exclude)
                reused += again
                e = {"kind": "meal", "meal_id": m["id"], "label": m["name"], "locked": 0}
            cur[d] = written[d] = e
            if e["kind"] == "meal":
                reserve(d, e)
        d += timedelta(1)

    for k in stale:
        if k not in written:
            c.execute("DELETE FROM plan WHERE day=?", (k.isoformat(),))
    for k, e in written.items():
        save_entry(c, k, e["kind"], e["meal_id"], e["label"], e["locked"])
    c.commit()
    return len([k for k in written if start <= k <= end]), reused, skipped_locked


@app.post("/api/generate")
def api_generate():
    body = request.get_json(silent=True) or {}
    start, end = parse_day(body.get("start")), parse_day(body.get("end"))
    if end < start or (end - start).days > 62:
        abort(400, "Choose a range of up to two months.")
    reroll = bool(body.get("reroll")) and start == end
    if reroll:
        r = db().execute("SELECT locked FROM plan WHERE day=?", (start.isoformat(),)).fetchone()
        if r and r["locked"]:
            abort(400, "This day is pinned. Unpin it first.")
    planned, reused, skipped_locked = generate(start, end, bool(body.get("replace")) or reroll, reroll)
    return jsonify(planned=planned, reused=reused, skipped_locked=skipped_locked)


def load_undo(c):
    """The stored undo snapshot, or None when there is none, it is too old, or the plan changed since."""
    row = c.execute("SELECT value FROM settings WHERE key='undo'").fetchone()
    if not row:
        return None
    snap = json.loads(row["value"])
    if time.time() - snap.get("t", 0) > UNDO_SECONDS:
        drop_undo(c)
        c.commit()
        return None
    return snap


@app.get("/api/suggest")
def api_suggest():
    """A few meals that fit a day, using the same rules as automatic planning."""
    d = parse_day(request.args.get("day"))
    n = to_int(request.args.get("n"), 1, 6, 3)
    c = db()
    S = get_settings(c)
    meals = load_planning_meals(c, S)
    cur = load_window(c, d, d)
    exclude = {e["meal_id"] for e in [cur.pop(d, None)] if e and e["meal_id"]}  # not the meal that is there now
    today = client_today().isoformat()
    last = {r["meal_id"]: r["day"] for r in c.execute(
        f"SELECT meal_id, MAX(day) AS day FROM ({SERVED_SQL}) WHERE day<=? GROUP BY meal_id", (today,))}
    wanted = {t for days, t in parse_rules(S["rules"]) if d.weekday() in days}
    out = []
    while len(out) < n and any(mid not in exclude for mid in meals):
        m, forced = pick(d, cur, meals, S, exclude)
        exclude.add(m["id"])
        why = []
        if m["id"] in last:
            ago = (date.fromisoformat(today) - date.fromisoformat(last[m["id"]])).days
            why.append("served today" if ago == 0 else "last served 1 day ago" if ago == 1 else f"last served {ago} days ago")
        else:
            why.append("never served")
        if m["weight"] == 2:
            why.append("a favourite")
        if m["weekdays"] and d.weekday() in m["weekdays"]:
            why.append(f"suits {d:%A}s")
        if m["months"] and d.month in m["months"]:
            why.append("in season")
        why += [f"{t} meal for {d:%A}s" for t in sorted(wanted & m["tags"])]
        out.append({"id": m["id"], "name": m["name"], "category": m["category"], "why": why,
                    "last_served": last.get(m["id"]), "repeat": bool(forced)})
    return jsonify(out)


@app.post("/api/undo")
def api_undo():
    c = db()
    snap = load_undo(c)
    if not snap:
        abort(400, "Nothing to undo.")
    c.execute("DELETE FROM plan WHERE day BETWEEN ? AND ?", (snap["lo"], snap["hi"]))
    for r in snap["rows"]:
        c.execute("INSERT OR REPLACE INTO plan(day,kind,meal_id,label,locked,ing_snapshot,cook) VALUES (?,?,?,?,?,?,?)",
                  (r["day"], r["kind"], r["meal_id"], r["label"], r["locked"], r.get("ing_snapshot", ""), r.get("cook", "")))
    c.execute("DELETE FROM settings WHERE key='undo'")
    c.commit()
    return jsonify(ok=True, restored=len(snap["rows"]))


@app.get("/api/undo")
def api_can_undo():
    return jsonify(available=load_undo(db()) is not None)


# ---------------------------------------------------------------- shopping list

@app.get("/api/shopping")
def shopping():
    c = db()
    S = get_settings(c)
    ws = week_start_of(parse_day(request.args.get("week")), S["week_start"])
    weeks = to_int(request.args.get("weeks"), 1, 6, 1)
    we = ws + timedelta(7 * weeks - 1)
    scope = f"{ws.isoformat()}:{weeks}"
    part = ("SELECT p.day AS day, COALESCE(m.name, p.label) AS name, COALESCE(m.ingredients, p.ing_snapshot) AS ingredients, "
            "COALESCE(m.servings, 0) AS servings FROM {t} p LEFT JOIN meals m ON m.id = p.meal_id "
            "WHERE p.kind='meal' AND p.day BETWEEN ? AND ?")
    rows = c.execute(
        f"SELECT * FROM ({part.format(t='plan')} UNION ALL {part.format(t='plan_lunch')}) ORDER BY day",
        (ws.isoformat(), we.isoformat()) * 2,
    ).fetchall()  # leftover days are kind='leftover' and never contribute
    syn, system = build_synonyms(S["synonyms"]), S["unit_system"]
    pantry = {parse_ingredient(p, (), syn)[0] for p in S["pantry"]}
    extra_units = set(S["units"])
    items, names, hidden = {}, [], set()
    for r in rows:
        if r["name"] not in names:
            names.append(r["name"])
        factor = S["household"] / r["servings"] if S["household"] and r["servings"] else 1
        for line in r["ingredients"].split("\n"):
            if not line:
                continue
            key, name, qty, unit, sym = parse_ingredient_full(line, extra_units, syn, system)
            if key in pantry:
                hidden.add(key)
                continue
            it = items.setdefault(key, {"key": key, "name": name, "qty": {}, "plain": 0, "sym": {}})
            if qty is None:
                it["plain"] += 1
            else:
                it["qty"][unit] = it["qty"].get(unit, 0) + qty * factor
                if sym:
                    it["sym"].setdefault(unit, {})[sym] = it["sym"].get(unit, {}).get(sym, 0) + 1
    done = {r["key"] for r in c.execute("SELECT key FROM checked WHERE scope=?", (scope,))}
    out = []
    for it in items.values():
        parts = [fmt_qty(q, u, system, max(it["sym"].get(u, {"": 0}), key=lambda k: it["sym"].get(u, {"": 0})[k]))
                 for u, q in it["qty"].items()]
        if parts and it["plain"]:
            parts.append(f"+ {it['plain']} more")
        elif it["plain"] > 1:
            parts.append(f"×{it['plain']}")
        out.append({"key": it["key"], "name": it["name"], "amount": " + ".join(parts).replace("+ + ", "+ "),
                    "checked": it["key"] in done})
    out.sort(key=lambda i: i["name"].lower())
    extras = [{"id": r["id"], "name": r["name"], "checked": f"extra:{r['id']}" in done}
              for r in c.execute("SELECT id, name FROM extras WHERE scope=? ORDER BY id", (scope,))]
    return jsonify(start=ws.isoformat(), end=we.isoformat(), weeks=weeks, scope=scope, meals=names, items=out,
                   extras=extras, hidden=len(hidden), household=S["household"])


@app.put("/api/shopping/check")
def shopping_check():
    b = request.get_json(silent=True) or {}
    scope, key = str(b.get("scope") or "")[:40], str(b.get("key") or "")[:200]
    if not scope or not key:
        abort(400, "Missing item.")
    if b.get("checked"):
        db().execute("INSERT OR IGNORE INTO checked(scope,key) VALUES (?,?)", (scope, key))
    else:
        db().execute("DELETE FROM checked WHERE scope=? AND key=?", (scope, key))
    prune_shopping(db())
    db().commit()
    return jsonify(ok=True)


@app.post("/api/shopping/extras")
def extras_add():
    b = request.get_json(silent=True) or {}
    scope, name = str(b.get("scope") or "")[:40], re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:100]
    if not scope or not name:
        abort(400, "Write what to add.")
    cur = db().execute("INSERT INTO extras(scope,name) VALUES (?,?)", (scope, name))
    prune_shopping(db())
    db().commit()
    return jsonify(id=cur.lastrowid), 201


@app.delete("/api/shopping/extras/<int:eid>")
def extras_delete(eid):
    db().execute("DELETE FROM extras WHERE id=?", (eid,))
    db().execute("DELETE FROM checked WHERE key=?", (f"extra:{eid}",))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- backup, restore, calendar feed

@app.get("/api/backup")
def backup():
    c = db()
    data = {
        "format": "mealplanner-backup", "version": SCHEMA_VERSION,
        "meals": [dict(r) for r in c.execute("SELECT * FROM meals ORDER BY id")],
        "plan": [dict(r) for r in c.execute("SELECT * FROM plan ORDER BY day")],
        "lunch": [dict(r) for r in c.execute("SELECT * FROM plan_lunch ORDER BY day")],
        "settings": get_settings(c),
        "checked": [dict(r) for r in c.execute("SELECT * FROM checked")],
        "extras": [dict(r) for r in c.execute("SELECT * FROM extras ORDER BY id")],
    }
    return Response(json.dumps(data, indent=1, ensure_ascii=False), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=mealplanner-{date.today().isoformat()}.json"})


@app.post("/api/restore")
def restore():
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or d.get("format") != "mealplanner-backup" or not isinstance(d.get("meals"), list):
        abort(400, "This is not a meal planner backup file.")
    c = db()
    try:
        for t in ("plan", "plan_lunch", "meals", "checked", "extras", "settings"):
            c.execute(f"DELETE FROM {t}")
        ids = set()
        for m in d["meals"]:
            mid = to_int(m.get("id"), 1, 2 ** 53, 0)
            if not mid or mid in ids:
                raise ValueError("A meal has a missing or duplicate id.")
            ids.add(mid)
            insert_meal(c, clean_meal(m), mid)
        for r in d.get("plan") or []:
            day = parse_day(r.get("day"))
            if r.get("kind") not in ("meal", "leftover", "custom"):
                raise ValueError("A planned day has an unknown type.")
            c.execute("INSERT INTO plan(day,kind,meal_id,label,locked,ing_snapshot,cook) VALUES (?,?,?,?,?,?,?)",
                      (day.isoformat(), r["kind"], r.get("meal_id"), str(r.get("label") or "")[:120],
                       int(bool(r.get("locked"))), str(r.get("ing_snapshot") or ""), str(r.get("cook") or "")[:40]))
        for r in d.get("lunch") or []:
            day = parse_day(r.get("day"))
            if r.get("kind") not in ("meal", "leftover", "custom"):
                raise ValueError("A lunch has an unknown type.")
            c.execute("INSERT INTO plan_lunch(day,kind,meal_id,label,ing_snapshot) VALUES (?,?,?,?,?)",
                      (day.isoformat(), r["kind"], r.get("meal_id"), str(r.get("label") or "")[:120],
                       str(r.get("ing_snapshot") or "")))
        for r in d.get("checked") or []:
            c.execute("INSERT OR IGNORE INTO checked(scope,key) VALUES (?,?)", (str(r["scope"]), str(r["key"])))
        for r in d.get("extras") or []:
            c.execute("INSERT INTO extras(id,scope,name) VALUES (?,?,?)", (int(r["id"]), str(r["scope"]), str(r["name"])[:100]))
        save_settings(c, clean_settings(d.get("settings") or {}))
        c.commit()
    except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
        c.rollback()
        abort(400, f"The backup could not be restored: {e}")
    return jsonify(ok=True, meals=len(d["meals"]), days=len(d.get("plan") or []) + len(d.get("lunch") or []))


def _ics(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@app.get("/calendar.ics")
def calendar_feed():
    today = date.today()
    rows = db().execute(
        "SELECT p.day, p.kind, COALESCE(m.name, p.label) AS name, m.url AS url FROM plan p "
        "LEFT JOIN meals m ON m.id = p.meal_id WHERE p.day BETWEEN ? AND ? ORDER BY p.day",
        ((today - timedelta(60)).isoformat(), (today + timedelta(400)).isoformat())).fetchall()
    lunches = db().execute(
        "SELECT p.day, p.kind, COALESCE(m.name, p.label) AS name, m.url AS url FROM plan_lunch p "
        "LEFT JOIN meals m ON m.id = p.meal_id WHERE p.day BETWEEN ? AND ? ORDER BY p.day",
        ((today - timedelta(60)).isoformat(), (today + timedelta(400)).isoformat())).fetchall()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Family Meal Planner//EN", "CALSCALE:GREGORIAN",
           "X-WR-CALNAME:Meal plan"]
    for slot, group in (("", rows), ("-lunch", lunches)):
        for r in group:
            d = date.fromisoformat(r["day"])
            summary = f"Leftovers: {r['name']}" if r["kind"] == "leftover" else r["name"]
            if slot:
                summary = f"Lunch: {summary}"
            out += ["BEGIN:VEVENT", f"UID:{r['day']}{slot}@mealplanner", f"DTSTAMP:{stamp}",
                    f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(1):%Y%m%d}",
                    f"SUMMARY:{_ics(summary)}"]
            if r["url"]:
                out.append(f"URL:{r['url']}")
            out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return Response("\r\n".join(out) + "\r\n", mimetype="text/calendar; charset=utf-8")


# ---------------------------------------------------------------- pages

@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/sw.js")
def service_worker():
    resp = app.send_static_file("sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    return app.send_static_file("manifest.webmanifest")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
