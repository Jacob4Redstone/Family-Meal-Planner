import json
import random
import sqlite3
import time
from datetime import date, timedelta

import pytest

from helpers import appmod, day, mk, plan_of  # noqa: E402  (also puts the app on sys.path)


# ---------------------------------------------------------------- parsing

def test_import_keeps_decimal_commas():
    m = appmod.parse_import("Soup\nIngredients: 1,5 dl milk, 2 onions; salt")[0]
    assert [i.strip() for i in m["ingredients"]] == ["1,5 dl milk", "2 onions", "salt"]


def test_import_extra_fields_and_weight():
    m = appmod.parse_import("Stew | Meat | yes\nServings: 4\nPrep: 45\nWeekdays: Sat, Sun\nWeight: favourite\n- Beef")[0]
    assert (m["servings"], m["prep_minutes"], m["weekdays"], m["weight"]) == ("4", "45", "Sat, Sun", 2)
    assert m["ingredients"] == ["- Beef"]  # the leading dash is cleaned later by clean_meal


@pytest.mark.parametrize("line,qty,unit,key", [
    ("1/2 onion", 0.5, "", "onion"),
    ("1 1/2 cups flour", 1.5, "cup", "flour"),
    ("½ cucumber", 0.5, "", "cucumber"),
    ("1½ l milk", 1500, "ml", "milk"),
    ("2 kg potatoes", 2000, "g", "potato"),
    ("500 g minced beef", 500, "g", "minced beef"),
    ("1,5 dl cream", 150, "ml", "cream"),
    ("3 stk eggs", 3, "pcs", "egg"),
    ("Onion", None, "", "onion"),
    ("1/0 onion", None, "", "1/0 onion"),
])
def test_parse_ingredient(line, qty, unit, key):
    k, _name, q, u = appmod.parse_ingredient(line)
    assert (k, u) == (key, unit)
    assert q == pytest.approx(qty) if qty is not None else q is None


def test_extra_units_and_singular():
    assert appmod.parse_ingredient("2 bundles dill", {"bundles"})[2:] == (2, "bundles")
    assert appmod.singular("hummus") == "hummus"
    assert appmod.singular("tomatoes") == "tomato"
    assert appmod.singular("berries") == "berry"
    assert appmod.fmt_qty(1500, "g") == "1.5 kg" and appmod.fmt_qty(250, "ml") == "250 ml"


# ---------------------------------------------------------------- security and basics

def test_requires_custom_header(client):
    r = appmod.app.test_client().post("/api/meals/import", data="Evil", content_type="text/plain")
    assert r.status_code == 403
    assert client.get("/api/meals").get_json() == []


def test_auth_password(client, monkeypatch):
    monkeypatch.setattr(appmod, "AUTH_PASSWORD", "secret")
    assert client.get("/api/meals").status_code == 401
    assert client.get("/healthz").status_code == 200
    ok = client.get("/api/meals", headers={"Authorization": "Basic " + __import__("base64").b64encode(b"u:secret").decode()})
    assert ok.status_code == 200


def test_meal_crud_and_validation(client):
    mid = mk(client, "Pasta", category="Pasta", weekdays=["mon", "Tue"], weight=2, servings=4, url="https://x.test/r")
    m = client.get("/api/meals").get_json()[0]
    assert m["weekdays"] == [0, 1] and m["weight"] == 2 and m["servings"] == 4 and m["times_served"] == 0
    assert client.post("/api/meals", json={"name": "pasta"}).status_code == 409
    assert client.post("/api/meals", json={"name": "X", "url": "javascript:alert(1)"}).status_code == 400
    assert client.put(f"/api/meals/{mid}", json={"name": ""}).status_code == 400


def test_migration_from_v1_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(appmod.SCHEMA_V1)
    conn.execute("INSERT INTO meals(name,category,leftovers,ingredients) VALUES ('Old','Soup',1,'Leek')")
    conn.execute("INSERT INTO plan VALUES ('2030-01-07','meal',1,'Old')")
    conn.commit()
    conn.close()
    monkeypatch.setitem(appmod.app.config, "DB_PATH", str(path))
    appmod.init_db()
    appmod.init_db()  # idempotent
    c = appmod.app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "fetch"
    m = c.get("/api/meals").get_json()[0]
    assert m["name"] == "Old" and m["leftovers"] and m["leftover_days"] == 1 and m["weight"] == 1
    assert plan_of(c, "2030-01-07", "2030-01-07")["2030-01-07"]["locked"] is False
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == appmod.SCHEMA_VERSION


# ---------------------------------------------------------------- day editing and leftovers

def test_leftover_reserved_and_freed(client):
    a = mk(client, "Roast", leftovers=True)
    b = mk(client, "Fish")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    p = plan_of(client, day(0), day(2))
    assert p[day(1)]["kind"] == "leftover" and p[day(1)]["name"] == "Roast"
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": b})
    assert day(1) not in plan_of(client, day(0), day(2))


def test_leftovers_never_overwrite_next_day(client):
    a = mk(client, "Roast", leftovers=True)
    client.put(f"/api/plan/{day(1)}", json={"kind": "custom", "label": "Eating out"})
    r = client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    assert r.get_json()["skipped"] == [day(1)]
    assert plan_of(client, day(1), day(1))[day(1)]["name"] == "Eating out"


def test_multi_day_leftovers_and_edit_meal(client):
    a = mk(client, "Stew", leftovers=True, leftover_days=2)
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    p = plan_of(client, day(0), day(3))
    assert [p[day(i)]["kind"] for i in (0, 1, 2)] == ["meal", "leftover", "leftover"] and day(3) not in p
    client.put(f"/api/meals/{a}", json={"name": "Stew", "leftovers": True, "leftover_days": 1})
    assert list(plan_of(client, day(0), day(3))) == [day(0), day(1)]
    client.put(f"/api/meals/{a}", json={"name": "Stew 2", "leftovers": False})
    p = plan_of(client, day(0), day(3))
    assert list(p) == [day(0)] and p[day(0)]["name"] == "Stew 2"
    client.put(f"/api/meals/{a}", json={"name": "Stew 2", "leftovers": True, "leftover_days": 3})
    assert len(plan_of(client, day(0), day(5))) == 4


def test_pinned_day_flag(client):
    a = mk(client, "A")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a, "locked": True})
    assert plan_of(client, day(0), day(0))[day(0)]["locked"] is True
    # editing without the flag keeps the pin
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    assert plan_of(client, day(0), day(0))[day(0)]["locked"] is True


def test_swap_moves_meals_with_their_leftovers(client):
    a = mk(client, "Roast", leftovers=True)
    b = mk(client, "Fish")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(4)}", json={"kind": "meal", "meal_id": b})
    assert client.post("/api/plan/swap", json={"a": day(1), "b": day(4)}).status_code == 400  # a leftover day
    assert client.post("/api/plan/swap", json={"a": day(0), "b": day(4)}).status_code == 200
    p = plan_of(client, day(0), day(6))
    assert p[day(0)]["name"] == "Fish" and p[day(4)]["name"] == "Roast"
    assert day(1) not in p and p[day(5)]["kind"] == "leftover"


def test_delete_meal_keeps_calendar_and_shopping(client):
    a = mk(client, "Soup", ingredients=["2 onions", "Leek"])
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.delete(f"/api/meals/{a}")
    assert plan_of(client, day(0), day(0))[day(0)]["name"] == "Soup"
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    assert {i["name"] for i in s["items"]} == {"Onions", "Leek"} and s["meals"] == ["Soup"]


# ---------------------------------------------------------------- planning

def test_generate_respects_gap_and_leftovers(client):
    for i in range(30):
        mk(client, f"Meal {i}", leftovers=(i % 5 == 0))
    r = client.post("/api/generate", json={"start": day(0), "end": day(27)}).get_json()
    assert r["reused"] == 0
    p = plan_of(client, day(0), day(40))
    served = [(d, e["meal_id"]) for d, e in sorted(p.items()) if e["kind"] == "meal"]
    for i, (d1, m1) in enumerate(served):
        for d2, m2 in served[i + 1:]:
            if m1 == m2:
                assert (date.fromisoformat(d2) - date.fromisoformat(d1)).days >= appmod.GAP_DAYS
    for d, e in p.items():  # every leftover follows its meal
        prev = p.get((date.fromisoformat(d) - timedelta(1)).isoformat())
        assert e["kind"] != "leftover" or (prev and prev["meal_id"] == e["meal_id"])


def test_generate_small_pool_reports_repeats(client):
    mk(client, "A")
    mk(client, "B")
    r = client.post("/api/generate", json={"start": day(0), "end": day(5)}).get_json()
    assert r["planned"] == 6 and r["reused"] > 0


def test_generate_spills_leftovers_and_replace_removes_them(client):
    a = mk(client, "Only", leftovers=True)
    client.post("/api/generate", json={"start": day(0), "end": day(0)})
    assert plan_of(client, day(0), day(2))[day(1)]["kind"] == "leftover"
    mk(client, "Other")
    # replacing the range drops the spilled leftover of the old meal
    client.post("/api/generate", json={"start": day(0), "end": day(0), "replace": True, "reroll": True})
    p = plan_of(client, day(0), day(2))
    assert p[day(0)]["meal_id"] != a and (day(1) not in p)


def test_reroll_works_on_a_leftover_day(client):
    a = mk(client, "Roast", leftovers=True)
    mk(client, "Fish")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    r = client.post("/api/generate", json={"start": day(1), "end": day(1), "replace": True, "reroll": True})
    assert r.status_code == 200
    e = plan_of(client, day(1), day(1))[day(1)]
    assert e["kind"] == "meal" and e["name"] == "Fish"  # a real meal, and not the same one again


def test_replace_keeps_pinned_days(client):
    for i in range(10):
        mk(client, f"M{i}")
    first = mk(client, "Pinned meal")
    client.put(f"/api/plan/{day(2)}", json={"kind": "meal", "meal_id": first, "locked": True})
    r = client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True}).get_json()
    assert r["skipped_locked"] == 1
    assert plan_of(client, day(2), day(2))[day(2)]["name"] == "Pinned meal"
    assert client.post("/api/generate", json={"start": day(2), "end": day(2), "replace": True, "reroll": True}).status_code == 400


def test_undo_restores_previous_plan(client):
    for i in range(5):
        mk(client, f"M{i}")
    client.post("/api/generate", json={"start": day(0), "end": day(6)})
    before = plan_of(client, day(0), day(6))
    client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True})
    assert client.get("/api/undo").get_json()["available"] is True
    assert client.post("/api/undo").status_code == 200
    assert plan_of(client, day(0), day(6)) == before
    assert client.post("/api/undo").status_code == 400


def test_fixed_days_and_weekday_rules(client):
    client.put("/api/settings", json={"slots": ["Friday = Takeaway"]})
    only_sat = mk(client, "Weekend roast", weekdays=["sat"])
    for i in range(8):
        mk(client, f"Weekday {i}", weekdays=["mon", "tue", "wed", "thu", "fri", "sun"])
    client.post("/api/generate", json={"start": day(0), "end": day(6)})
    p = plan_of(client, day(0), day(6))
    assert p[day(4)]["name"] == "Takeaway" and p[day(4)]["kind"] == "custom"
    assert p[day(5)]["meal_id"] == only_sat
    assert all(p[day(i)]["meal_id"] != only_sat for i in (0, 1, 2, 3, 6))


def test_category_limit_per_week(client):
    client.put("/api/settings", json={"quotas": ["Pasta = 2"]})
    for i in range(10):
        mk(client, f"Pasta {i}", category="Pasta")
    for i in range(10):
        mk(client, f"Other {i}", category=f"Cat{i}")
    for seed in range(5):
        random.seed(seed)
        client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True})
        meals = client.get("/api/meals").get_json()
        cats = {m["id"]: m["category"] for m in meals}
        n = sum(1 for e in plan_of(client, day(0), day(6)).values() if cats.get(e["meal_id"]) == "Pasta")
        assert n <= 2


def test_favourites_are_picked_more_often(client):
    fav = mk(client, "Fav", weight=2)
    rare = mk(client, "Rare", weight=0)
    c = {fav: 0, rare: 0}
    conn = _row_conn()
    meals = appmod.load_planning_meals(conn, appmod.DEFAULT_SETTINGS)
    conn.close()
    for _ in range(300):
        m, _ = appmod.pick(date(2030, 1, 7), {}, meals, appmod.DEFAULT_SETTINGS)
        c[m["id"]] += 1
    assert c[fav] > c[rare] * 3


def _row_conn():
    conn = sqlite3.connect(appmod.app.config["DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- shopping

def test_shopping_merges_units_and_skips_leftovers(client):
    a = mk(client, "Beef stew", leftovers=True, ingredients=["500 g beef", "2 onions", "salt", "1/2 l stock"])
    b = mk(client, "Burgers", ingredients=["0,5 kg beef", "1 onion", "Onion", "250 ml stock"])
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})  # day(1) = leftovers
    client.put(f"/api/plan/{day(2)}", json={"kind": "meal", "meal_id": b})
    s = client.get(f"/api/shopping?week={day(3)}").get_json()
    items = {i["name"]: i["amount"] for i in s["items"]}
    assert items["Beef"] == "1 kg"
    assert items["Onions"] == "3 + 1 more"
    assert items["Stock"] == "750 ml" and "Salt" in items
    assert s["start"] == day(0) and s["end"] == day(6)


def test_shopping_pantry_household_scaling_and_weeks(client):
    a = mk(client, "Stew", servings=2, ingredients=["200 g rice", "Salt", "2 tomatoes"])
    client.put(f"/api/plan/{day(8)}", json={"kind": "meal", "meal_id": a})
    client.put("/api/settings", json={"household": 4, "pantry": ["salt"]})
    s = client.get(f"/api/shopping?week={day(0)}&weeks=2").get_json()
    items = {i["name"]: i["amount"] for i in s["items"]}
    assert items == {"Rice": "400 g", "Tomatoes": "4"} and s["hidden"] == 1 and s["end"] == day(13)
    assert client.get(f"/api/shopping?week={day(0)}&weeks=1").get_json()["items"] == []


def test_shopping_checks_and_extras_are_shared(client):
    a = mk(client, "Soup", ingredients=["Leek"])
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    client.put("/api/shopping/check", json={"scope": s["scope"], "key": "leek", "checked": True})
    eid = client.post("/api/shopping/extras", json={"scope": s["scope"], "name": "Soap"}).get_json()["id"]
    client.put("/api/shopping/check", json={"scope": s["scope"], "key": f"extra:{eid}", "checked": True})
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    assert s["items"][0]["checked"] is True and s["extras"] == [{"id": eid, "name": "Soap", "checked": True}]
    client.put("/api/shopping/check", json={"scope": s["scope"], "key": "leek", "checked": False})
    client.delete(f"/api/shopping/extras/{eid}")
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    assert s["items"][0]["checked"] is False and s["extras"] == []


def test_sunday_week_start(client):
    client.put("/api/settings", json={"week_start": 6})
    s = client.get(f"/api/shopping?week={day(2)}").get_json()  # Wednesday
    assert s["start"] == day(-1) and s["end"] == day(5)  # Sunday .. Saturday


def test_settings_validation(client):
    assert client.put("/api/settings", json={"slots": ["nonsense"]}).status_code == 400
    assert client.put("/api/settings", json={"quotas": ["Pasta = many"]}).status_code == 400
    s = client.put("/api/settings", json={"units": "Clove, bunch", "pantry": "Salt\n\nOil"}).get_json()
    assert s["units"] == ["clove", "bunch"] and s["pantry"] == ["Salt", "Oil"]


# ---------------------------------------------------------------- import, backup, feed

def test_import_update_existing(client):
    mk(client, "Lasagne", category="Old", ingredients=["Pasta"])
    text = "Lasagne | Italian | yes\n- Mince\n- Cheese\n\nNew dish | Soup | no"
    r = client.post("/api/meals/import", data=text, content_type="text/plain")
    assert r.get_json() == {"added": 1, "skipped": 1, "updated": 0}
    r = client.post("/api/meals/import?update=1", data="Lasagne | Italian | yes\n- Mince\nServings: 6", content_type="text/plain")
    assert r.get_json()["updated"] == 1
    m = next(m for m in client.get("/api/meals").get_json() if m["name"] == "Lasagne")
    assert (m["category"], m["leftovers"], m["ingredients"], m["servings"]) == ("Italian", True, ["Mince"], 6)


def test_backup_restore_roundtrip(client):
    a = mk(client, "Roast", leftovers=True, leftover_days=2, ingredients=["Beef"], weekdays=["sun"], notes="slow")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a, "locked": True})
    client.put("/api/settings", json={"household": 3, "slots": ["Fri = Pizza"]})
    backup = client.get("/api/backup").get_data(as_text=True)
    mk(client, "Junk")
    client.put(f"/api/plan/{day(9)}", json={"kind": "custom", "label": "junk"})
    r = client.post("/api/restore", data=backup, content_type="application/json")
    assert r.get_json() == {"ok": True, "meals": 1, "days": 3}
    assert [m["name"] for m in client.get("/api/meals").get_json()] == ["Roast"]
    assert client.get("/api/settings").get_json()["household"] == 3
    assert plan_of(client, day(0), day(9))[day(0)]["locked"] is True
    assert json.loads(client.get("/api/backup").get_data(as_text=True))["meals"][0]["notes"] == "slow"


def test_restore_rejects_garbage_and_keeps_data(client):
    mk(client, "Keep me")
    assert client.post("/api/restore", json={"hello": 1}).status_code == 400
    bad = {"format": "mealplanner-backup", "meals": [{"id": 1, "name": "A"}, {"id": 1, "name": "B"}]}
    assert client.post("/api/restore", json=bad).status_code == 400
    assert [m["name"] for m in client.get("/api/meals").get_json()] == ["Keep me"]


def test_calendar_feed(client):
    a = mk(client, "Roast, with; chars", leftovers=True)
    d = date.today()
    client.put(f"/api/plan/{d.isoformat()}", json={"kind": "meal", "meal_id": a})
    ics = client.get("/calendar.ics").get_data(as_text=True)
    assert ics.startswith("BEGIN:VCALENDAR") and "SUMMARY:Roast\\, with\\; chars" in ics
    assert "SUMMARY:Leftovers: Roast" in ics and f"DTSTART;VALUE=DATE:{d:%Y%m%d}" in ics


def test_page_and_pwa_files(client):
    assert b"Meal planner" in client.get("/").data
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").headers["Cache-Control"] == "no-cache"
    assert client.get("/api/config").get_json()["gap_days"] == appmod.GAP_DAYS


# ---------------------------------------------------------------- recipe from a link

def test_url_import_is_off_by_default(client):
    assert client.post("/api/meals/from-url", json={"url": "https://example.com"}).status_code == 404


def test_url_import_blocks_private_addresses(client, monkeypatch):
    monkeypatch.setattr(appmod, "ENABLE_URL_IMPORT", True)
    for url in ("http://127.0.0.1:8080/", "http://localhost/", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://x.test"):
        r = client.post("/api/meals/from-url", json={"url": url})
        assert r.status_code == 400, url


def test_parse_recipe_json_ld():
    page = """<html><script type="application/ld+json">{"@graph":[{"@type":"WebPage"},
      {"@type":["Recipe"],"name":"Pancakes &amp; more","recipeCategory":["Breakfast"],"recipeYield":["4 servings"],
       "totalTime":"PT1H15M","recipeIngredient":["2 eggs","200 g flour"]}]}</script></html>"""
    r = appmod.parse_recipe(page, "https://x.test/p")
    assert r == {"name": "Pancakes & more", "category": "Breakfast", "ingredients": ["2 eggs", "200 g flour"],
                 "servings": 4, "prep_minutes": 75, "url": "https://x.test/p"}
    assert appmod.parse_recipe("<html>nothing</html>") is None


# ================================================================ round 2: regression tests

def can_undo(c):
    return c.get("/api/undo").get_json()["available"]


def planned_month(c):
    for i in range(6):
        mk(c, f"M{i}")
    c.post("/api/generate", json={"start": day(0), "end": day(6)})
    assert can_undo(c)


def test_undo_is_dropped_by_other_changes(client):
    planned_month(client)
    client.put(f"/api/plan/{day(3)}", json={"kind": "custom", "label": "Dinner at Anna's"})
    assert not can_undo(client)
    assert client.post("/api/undo").status_code == 400
    assert plan_of(client, day(3), day(3))[day(3)]["name"] == "Dinner at Anna's"  # not clobbered

    planned_month_again = client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True})
    assert planned_month_again.status_code == 200 and can_undo(client)
    client.post("/api/plan/swap", json={"a": day(0), "b": day(1)})
    assert not can_undo(client)

    client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True})
    mid = next(iter(client.get("/api/meals").get_json()))["id"]
    client.delete(f"/api/meals/{mid}")
    assert not can_undo(client)


def test_undo_expires(client, monkeypatch):
    planned_month(client)
    real = time.time()
    monkeypatch.setattr(appmod.time, "time", lambda: real + appmod.UNDO_SECONDS + 5)
    assert not can_undo(client)
    assert client.post("/api/undo").status_code == 400


def test_undo_still_works_right_after_planning(client):
    planned_month(client)
    assert client.post("/api/undo").status_code == 200


def test_meal_edit_keeps_undo_unless_leftovers_change(client):
    planned_month(client)
    a = client.get("/api/meals").get_json()[0]
    client.put(f"/api/meals/{a['id']}", json={"name": a["name"] + " renamed"})
    assert can_undo(client)  # no plan rows changed


def test_leftover_toggle_does_not_touch_the_past(client):
    a = mk(client, "Retro")
    past, future = date.today() - timedelta(40), date.today() + timedelta(10)
    for d in (past, future):
        client.put(f"/api/plan/{d.isoformat()}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/meals/{a}", json={"name": "Retro", "leftovers": True})
    p = plan_of(client, (past - timedelta(1)).isoformat(), (future + timedelta(2)).isoformat())
    assert (past + timedelta(1)).isoformat() not in p  # history unchanged
    assert p[(future + timedelta(1)).isoformat()]["kind"] == "leftover"
    # turning it off again also leaves history alone and removes the future leftover
    client.put(f"/api/meals/{a}", json={"name": "Retro", "leftovers": False})
    assert (future + timedelta(1)).isoformat() not in plan_of(client, future.isoformat(), (future + timedelta(2)).isoformat())


@pytest.mark.parametrize("line,key,qty,unit", [
    ("2 tablespoons olive oil", "olive oil", 2, "tbsp"),
    ("200 grams flour", "flour", 200, "g"),
    ("1 onion, finely chopped", "onion", 1, ""),
    ("salt, to taste", "salt", None, ""),
    ("3-4 cloves garlic", "garlic", 4, "clove"),
    ("3 to 4 cloves garlic", "garlic", 4, "clove"),
    ("1 (14 oz) can beans", "bean", 1, "can"),
    ("2 x 400 g tomatoes", "tomato", 800, "g"),
    ("2 x tomatoes", "tomato", 2, ""),
    ("2 cups of flour", "flour", 2, "cup"),
    ("1 head of lettuce", "lettuce", 1, "head"),
    ("1,5 dl cream", "cream", 150, "ml"),
    ("1/2 tsp. salt", "salt", 0.5, "tsp"),
    ("2 tomatoes", "tomato", 2, ""),
    ("(optional)", "(optional)", None, ""),
])
def test_recipe_site_ingredient_lines(line, key, qty, unit):
    k, _name, q, u = appmod.parse_ingredient(line)
    assert (k, u) == (key, unit)
    assert q == pytest.approx(qty) if qty is not None else q is None


def test_recipe_lines_merge_in_shopping_and_pluralise(client):
    a = mk(client, "Pasta", ingredients=["1 onion, finely chopped", "3-4 cloves garlic", "2 tablespoons olive oil"])
    b = mk(client, "Soup", ingredients=["2 onions", "2 cloves garlic", "1 tbsp olive oil"])
    for i, m in enumerate((a, b)):
        client.put(f"/api/plan/{day(i * 2)}", json={"kind": "meal", "meal_id": m})
    items = {i["name"]: i["amount"] for i in client.get(f"/api/shopping?week={day(0)}").get_json()["items"]}
    assert items["Onion"] == "3" or items.get("Onions") == "3"
    assert items["Garlic"] == "6 cloves" and items["Olive oil"] == "3 tbsp"
    assert appmod.fmt_qty(1, "clove") == "1 clove" and appmod.fmt_qty(2, "bunch") == "2 bunches"


def test_swap_reports_lost_leftover_days(client):
    a = mk(client, "Roast", leftovers=True)
    b = mk(client, "Fish")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(4)}", json={"kind": "meal", "meal_id": b})
    client.put(f"/api/plan/{day(5)}", json={"kind": "custom", "label": "Out"})
    r = client.post("/api/plan/swap", json={"a": day(0), "b": day(4)}).get_json()
    assert r["ok"] and r["skipped"] == [day(5)]
    assert plan_of(client, day(5), day(5))[day(5)]["name"] == "Out"


def test_missing_meal_is_404(client):
    assert client.put("/api/meals/9999", json={"name": "Ghost"}).status_code == 404
    assert client.get("/api/meals").get_json() == []


def test_partial_settings_update_keeps_the_rest(client):
    client.put("/api/settings", json={"household": 4, "pantry": ["salt"], "slots": ["Fri = Pizza"]})
    s = client.put("/api/settings", json={"week_start": 6}).get_json()
    assert (s["household"], s["pantry"], s["slots"], s["week_start"]) == (4, ["salt"], ["Fri = Pizza"], 6)


def test_clearing_a_middle_leftover_day_removes_the_tail(client):
    a = mk(client, "Stew", leftovers=True, leftover_days=3)
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(2)}", json={"kind": "clear"})
    assert list(plan_of(client, day(0), day(5))) == [day(0), day(1)]
    # replacing it with another meal does the same
    b = mk(client, "Fish")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": b})
    assert list(plan_of(client, day(0), day(5))) == [day(0), day(1)]


def test_fill_does_not_resurrect_a_cleared_leftover_day_but_replace_does(client):
    a = mk(client, "Stew", leftovers=True)
    mk(client, "Other")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(1)}", json={"kind": "clear"})
    client.post("/api/generate", json={"start": day(1), "end": day(1)})
    e = plan_of(client, day(1), day(1))[day(1)]
    assert e["kind"] == "meal" and e["name"] == "Other"  # a fresh pick, not the leftover again
    client.put(f"/api/plan/{day(1)}", json={"kind": "clear"})
    client.post("/api/generate", json={"start": day(1), "end": day(1), "replace": True})
    assert plan_of(client, day(1), day(1))[day(1)]["kind"] == "leftover"


def test_served_counts_use_the_browsers_date(client):
    a = mk(client, "Soup")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    before = client.get(f"/api/meals?today={day(-1)}").get_json()[0]
    after = client.get(f"/api/meals?today={day(0)}").get_json()[0]
    assert (before["times_served"], before["last_served"]) == (0, None)
    assert (after["times_served"], after["last_served"]) == (1, day(0))
    assert client.get("/api/meals?today=nonsense").status_code == 400


def test_old_shopping_ticks_and_extras_are_pruned(client):
    old_scope = f"{(date.today() - timedelta(200)).isoformat()}:1"
    new_scope = f"{date.today().isoformat()}:1"
    conn = sqlite3.connect(appmod.app.config["DB_PATH"])
    conn.execute("INSERT INTO extras(id,scope,name) VALUES (1,?, 'old soap'), (2,?, 'new soap')", (old_scope, new_scope))
    conn.execute("INSERT INTO checked(scope,key) VALUES (?, 'leek'), (?, 'extra:1'), (?, 'extra:2'), (?, 'extra:99')",
                 (old_scope, new_scope, new_scope, new_scope))
    conn.commit()
    conn.close()
    client.put("/api/shopping/check", json={"scope": new_scope, "key": "milk", "checked": True})  # triggers pruning
    conn = sqlite3.connect(appmod.app.config["DB_PATH"])
    assert [r[0] for r in conn.execute("SELECT name FROM extras")] == ["new soap"]
    assert sorted(r[1] for r in conn.execute("SELECT * FROM checked")) == ["extra:2", "milk"]
    conn.close()


def test_week_start_change_keeps_extras_and_drops_ingredient_ticks(client):
    a = mk(client, "Soup", ingredients=["Leek"])
    d = date.today() + timedelta(30)
    d += timedelta((2 - d.weekday()) % 7)  # a Wednesday: the same week under both week starts
    client.put(f"/api/plan/{d.isoformat()}", json={"kind": "meal", "meal_id": a})
    s = client.get(f"/api/shopping?week={d.isoformat()}").get_json()
    client.put("/api/shopping/check", json={"scope": s["scope"], "key": "leek", "checked": True})
    eid = client.post("/api/shopping/extras", json={"scope": s["scope"], "name": "Soap"}).get_json()["id"]
    client.put("/api/shopping/check", json={"scope": s["scope"], "key": f"extra:{eid}", "checked": True})
    client.put("/api/settings", json={"week_start": 6})
    s2 = client.get(f"/api/shopping?week={d.isoformat()}").get_json()
    assert [i["checked"] for i in s2["items"]] == [False]  # ingredient tick dropped
    assert s2["extras"] == [{"id": eid, "name": "Soap", "checked": True}]  # extra and its tick followed the week
