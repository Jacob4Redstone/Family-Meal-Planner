"""Family meal planner: meals -> automatic planning -> leftovers -> weekly shopping lists."""
import os
import random
import re
import sqlite3
from datetime import date, timedelta

from flask import Flask, Response, abort, g, jsonify, request
from werkzeug.exceptions import HTTPException

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
GAP_DAYS = int(os.environ.get("GAP_DAYS", "21"))  # a meal may not repeat within this many days
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "mealplanner.db")

app = Flask(__name__, static_folder="static", static_url_path="/static")

SCHEMA = """
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
with sqlite3.connect(DB_PATH) as _c:
    _c.executescript(SCHEMA)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close(_exc):
    conn = g.pop("db", None)
    if conn:
        conn.close()


@app.errorhandler(HTTPException)
def _err(e):
    return jsonify(error=e.description), e.code


# ---------------------------------------------------------------- meals

def meal_dict(r):
    return {
        "id": r["id"],
        "name": r["name"],
        "category": r["category"],
        "leftovers": bool(r["leftovers"]),
        "ingredients": [i for i in r["ingredients"].split("\n") if i],
    }


def clean_ingredient(s):
    return re.sub(r"\s+", " ", s).strip().lstrip("-•* ").strip()


def clean_meal(d):
    name = (d.get("name") or "").strip()
    if not name:
        abort(400, "A meal needs a name.")
    ing = d.get("ingredients") or []
    if isinstance(ing, str):
        ing = ing.split("\n")
    ing = [i for i in (clean_ingredient(x) for x in ing) if i]
    return name[:120], (d.get("category") or "").strip()[:60], int(bool(d.get("leftovers"))), "\n".join(ing)


@app.get("/api/meals")
def list_meals():
    rows = db().execute("SELECT * FROM meals ORDER BY name COLLATE NOCASE").fetchall()
    return jsonify([meal_dict(r) for r in rows])


@app.post("/api/meals")
def add_meal():
    vals = clean_meal(request.get_json(silent=True) or {})
    try:
        cur = db().execute("INSERT INTO meals(name,category,leftovers,ingredients) VALUES (?,?,?,?)", vals)
        db().commit()
    except sqlite3.IntegrityError:
        abort(409, "A meal with this name already exists.")
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/meals/<int:mid>")
def edit_meal(mid):
    vals = clean_meal(request.get_json(silent=True) or {})
    try:
        db().execute("UPDATE meals SET name=?,category=?,leftovers=?,ingredients=? WHERE id=?", (*vals, mid))
        db().commit()
    except sqlite3.IntegrityError:
        abort(409, "A meal with this name already exists.")
    return jsonify(ok=True)


@app.delete("/api/meals/<int:mid>")
def delete_meal(mid):
    db().execute("DELETE FROM meals WHERE id=?", (mid,))
    db().commit()
    return jsonify(ok=True)


def truthy(s):
    return str(s).strip().lower() in ("yes", "y", "true", "1", "x")


def parse_import(text):
    """Blocks separated by blank lines. Either
         Name | Category | yes        followed by '- ingredient' lines
       or
         Name / Category: .. / Leftovers: .. / Ingredients: (list or comma separated)"""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        name, cat, lo, ings = lines[0], "", False, []
        if "|" in name:
            p = [x.strip() for x in name.split("|")]
            name, cat, lo = p[0], (p[1] if len(p) > 1 else ""), (len(p) > 2 and truthy(p[2]))
        for l in lines[1:]:
            low = l.lower()
            if low.startswith("category:"):
                cat = l.split(":", 1)[1].strip()
            elif low.startswith("leftovers:"):
                lo = truthy(l.split(":", 1)[1])
            elif low.startswith("ingredients:"):
                rest = l.split(":", 1)[1]
                ings += [x for x in rest.split(",") if x.strip()]
            else:
                ings.append(l)
        if name:
            out.append({"name": name, "category": cat, "leftovers": lo, "ingredients": ings})
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
    added = skipped = 0
    for m in parse_import(request.get_data(as_text=True)):
        try:
            db().execute("INSERT INTO meals(name,category,leftovers,ingredients) VALUES (?,?,?,?)", clean_meal(m))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    db().commit()
    return jsonify(added=added, skipped=skipped)


# ---------------------------------------------------------------- plan

def parse_day(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        abort(400, "Invalid date.")


@app.get("/api/plan")
def get_plan():
    start, end = parse_day(request.args.get("start")), parse_day(request.args.get("end"))
    rows = db().execute(
        "SELECT p.day, p.kind, p.meal_id, COALESCE(m.name, p.label) AS name, m.category AS category "
        "FROM plan p LEFT JOIN meals m ON m.id = p.meal_id WHERE p.day BETWEEN ? AND ? ORDER BY p.day",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.put("/api/plan/<day>")
def put_day(day):
    d = parse_day(day)
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    c = db()
    nxt = (d + timedelta(1)).isoformat()

    # Replacing a meal that reserved tomorrow as leftovers frees tomorrow again.
    old = c.execute("SELECT kind, meal_id FROM plan WHERE day=?", (day,)).fetchone()
    if old and old["kind"] == "meal":
        n = c.execute("SELECT kind, meal_id FROM plan WHERE day=?", (nxt,)).fetchone()
        if n and n["kind"] == "leftover" and n["meal_id"] == old["meal_id"]:
            c.execute("DELETE FROM plan WHERE day=?", (nxt,))

    if kind == "custom" and (body.get("label") or "").strip():
        c.execute("INSERT OR REPLACE INTO plan VALUES (?,?,?,?)", (day, "custom", None, body["label"].strip()[:80]))
    elif kind in ("meal", "leftover"):
        m = c.execute("SELECT * FROM meals WHERE id=?", (body.get("meal_id"),)).fetchone()
        if not m:
            abort(400, "Unknown meal.")
        c.execute("INSERT OR REPLACE INTO plan VALUES (?,?,?,?)", (day, kind, m["id"], m["name"]))
        if kind == "meal" and m["leftovers"]:
            c.execute("INSERT OR REPLACE INTO plan VALUES (?,?,?,?)", (nxt, "leftover", m["id"], m["name"]))
    else:  # empty the day
        c.execute("DELETE FROM plan WHERE day=?", (day,))
    c.commit()
    return jsonify(ok=True)


def pick(d, cur, meals):
    """Choose a meal for day d. Returns (meal, was_forced_repeat)."""
    used, near = {}, []
    for k, e in cur.items():
        gap = abs((k - d).days)
        if e["kind"] == "meal" and e["meal_id"] in meals and gap < GAP_DAYS:
            used[e["meal_id"]] = min(gap, used.get(e["meal_id"], gap))
        if 0 < gap <= 2 and e["meal_id"] in meals:
            near.append(meals[e["meal_id"]]["category"].lower())
    free = [m for m in meals.values() if m["id"] not in used]
    if free:  # prefer a category that hasn't just been served
        varied = [m for m in free if not m["category"] or m["category"].lower() not in near]
        return random.choice(varied or free), 0
    # Pool too small: reuse whichever meal was served longest ago.
    best = max(used.values())
    return random.choice([meals[i] for i, g_ in used.items() if g_ == best]), 1


def generate(start, end, replace):
    c = db()
    meals = {r["id"]: dict(r) for r in c.execute("SELECT * FROM meals")}
    if not meals:
        abort(400, "Add some meals first.")
    lo, hi = start - timedelta(GAP_DAYS), end + timedelta(GAP_DAYS + 1)
    cur = {
        date.fromisoformat(r["day"]): dict(r)
        for r in c.execute("SELECT * FROM plan WHERE day BETWEEN ? AND ?", (lo.isoformat(), hi.isoformat()))
    }
    after = end + timedelta(1)
    old_last = cur.get(end)
    if replace:
        for k in [k for k in cur if start <= k <= end]:
            del cur[k]

    written, reused = {}, 0
    d = start
    while d <= end:
        if d not in cur:
            prev = cur.get(d - timedelta(1))
            pm = meals.get(prev["meal_id"]) if prev and prev["kind"] == "meal" else None
            if pm and pm["leftovers"]:
                e = {"kind": "leftover", "meal_id": pm["id"], "label": pm["name"]}
            else:
                m, again = pick(d, cur, meals)
                reused += again
                e = {"kind": "meal", "meal_id": m["id"], "label": m["name"]}
            cur[d] = written[d] = e
        d += timedelta(1)

    # Leftovers from the last planned day spill into the day after the range.
    stale = None
    if replace and old_last and old_last["kind"] == "meal":
        n = cur.get(after)
        if n and n["kind"] == "leftover" and n["meal_id"] == old_last["meal_id"]:
            stale = after
            del cur[after]
    last = cur.get(end)
    if last and last["kind"] == "meal" and after not in cur:
        m = meals.get(last["meal_id"])
        if m and m["leftovers"]:
            cur[after] = written[after] = {"kind": "leftover", "meal_id": m["id"], "label": m["name"]}
    if stale and stale not in written:
        c.execute("DELETE FROM plan WHERE day=?", (stale.isoformat(),))

    c.executemany(
        "INSERT OR REPLACE INTO plan VALUES (?,?,?,?)",
        [(k.isoformat(), e["kind"], e["meal_id"], e["label"]) for k, e in written.items()],
    )
    c.commit()
    return len([k for k in written if start <= k <= end]), reused


@app.post("/api/generate")
def api_generate():
    body = request.get_json(silent=True) or {}
    start, end = parse_day(body.get("start")), parse_day(body.get("end"))
    if end < start or (end - start).days > 62:
        abort(400, "Choose a range of up to two months.")
    planned, reused = generate(start, end, bool(body.get("replace")))
    return jsonify(planned=planned, reused=reused)


# ---------------------------------------------------------------- shopping list

UNITS = {"g", "kg", "ml", "cl", "dl", "l", "tsp", "tbsp", "cup", "cups", "oz", "lb", "stk", "spsk", "tsk",
         "pcs", "can", "cans", "pack", "packs"}
QTY = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:[x×]\s+)?(.*)$", re.I)


def singular(name):
    words = name.lower().split()
    w = words[-1]
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("oes"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    words[-1] = w
    return " ".join(words)


def parse_ingredient(line):
    """'500 g minced beef' -> (key, 'minced beef', 500.0, 'g'); 'Onion' -> ('onion', 'Onion', None, '')."""
    line = clean_ingredient(line)
    qty, unit, name = None, "", line
    m = QTY.match(line)
    if m and m.group(2).strip():
        qty, name = float(m.group(1).replace(",", ".")), m.group(2).strip()
        first, _, rest = name.partition(" ")
        if first.lower().rstrip(".") in UNITS and rest:
            unit, name = first.lower().rstrip("."), rest.strip()
    return singular(name), name[:1].upper() + name[1:], qty, unit


def fmt_qty(q, unit):
    s = f"{q:g}"
    return f"{s} {unit}" if unit else s


@app.get("/api/shopping")
def shopping():
    ws = parse_day(request.args.get("week"))
    ws -= timedelta(ws.weekday())  # Monday
    we = ws + timedelta(6)
    rows = db().execute(
        "SELECT m.name, m.ingredients FROM plan p JOIN meals m ON m.id = p.meal_id "
        "WHERE p.kind='meal' AND p.day BETWEEN ? AND ? ORDER BY p.day",
        (ws.isoformat(), we.isoformat()),
    ).fetchall()  # leftover days are kind='leftover' and never contribute
    items, names = {}, []
    for r in rows:
        if r["name"] not in names:
            names.append(r["name"])
        for line in r["ingredients"].split("\n"):
            if not line:
                continue
            key, name, qty, unit = parse_ingredient(line)
            it = items.setdefault(key, {"key": key, "name": name, "qty": {}, "plain": 0})
            if qty is None:
                it["plain"] += 1
            else:
                it["qty"][unit] = it["qty"].get(unit, 0) + qty
    out = []
    for it in items.values():
        parts = [fmt_qty(q, u) for u, q in it["qty"].items()]
        if parts and it["plain"]:
            parts.append(f"+ {it['plain']} more")
        elif it["plain"] > 1:
            parts.append(f"×{it['plain']}")
        out.append({"key": it["key"], "name": it["name"], "amount": " + ".join(parts).replace("+ + ", "+ ")})
    out.sort(key=lambda i: i["name"].lower())
    return jsonify(start=ws.isoformat(), end=we.isoformat(), meals=names, items=out)


@app.get("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
