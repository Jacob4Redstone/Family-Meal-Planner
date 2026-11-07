import random
import sqlite3

import pytest
from helpers import appmod, day, mk, plan_of


# ---------------------------------------------------------------- parsing helpers

@pytest.mark.parametrize("text,expected", [
    ("Mon-Fri", {0, 1, 2, 3, 4}),
    ("Sat, Sun", {5, 6}),
    ("Fri-Mon", {4, 5, 6, 0}),
    ("friday", {4}),
    ("man-fre", {0, 1, 2, 3, 4}),
])
def test_parse_day_set(text, expected):
    assert appmod.parse_day_set(text) == expected


@pytest.mark.parametrize("text", ["", "funday", "Mon-Zzz"])
def test_parse_day_set_rejects_nonsense(text):
    with pytest.raises(ValueError):
        appmod.parse_day_set(text)


@pytest.mark.parametrize("text,expected", [
    ("Jun-Aug", [6, 7, 8]),
    ("Dec-Feb", [1, 2, 12]),
    ("3,4, 5", [3, 4, 5]),
    ("jan, Jul - Sep", [1, 7, 8, 9]),
    ([6, "7"], [6, 7]),
    ("nonsense", []),
    ("", []),
])
def test_parse_months(text, expected):
    assert appmod.parse_months(text) == expected


def test_parse_tags_and_rules():
    assert appmod.parse_tags("Quick, vegetarian ,, QUICK; kid-friendly") == ["kid-friendly", "quick", "vegetarian"]
    assert appmod.parse_rules(["Mon-Fri = quick, vegetarian"]) == [({0, 1, 2, 3, 4}, "quick"), ({0, 1, 2, 3, 4}, "vegetarian")]
    assert appmod.parse_cooks(["Mon-Wed = Anna", "Thu: Ben"]) == {0: "Anna", 1: "Anna", 2: "Anna", 3: "Ben"}
    with pytest.raises(ValueError):
        appmod.parse_rules(["quick on weekdays"])
    with pytest.raises(ValueError):
        appmod.parse_cooks(["Anna"])


def test_settings_validate_rules_cooks_and_avoid(client):
    assert client.put("/api/settings", json={"rules": ["nonsense"]}).status_code == 400
    assert client.put("/api/settings", json={"cooks": ["nonsense"]}).status_code == 400
    body = {"avoid": "Peanut\n  Mushroom ", "rules": "Mon-Fri = quick", "cooks": "Sat = Ben"}
    s = client.put("/api/settings", json=body).get_json()
    assert (s["avoid"], s["rules"], s["cooks"]) == (["peanut", "mushroom"], ["Mon-Fri = quick"], ["Sat = Ben"])


# ---------------------------------------------------------------- meal fields

def test_new_meal_fields_roundtrip_through_api_import_and_backup(client):
    mid = mk(client, "Curry", tags="Quick, Spicy", months="Oct-Dec", prep_ahead="Soak the lentils", prep_ahead_days=2)
    m = client.get("/api/meals").get_json()[0]
    fields = (m["tags"], m["months"], m["prep_ahead"], m["prep_ahead_days"])
    assert fields == (["quick", "spicy"], [10, 11, 12], "Soak the lentils", 2)

    text = "Soup | Soup | no\nTags: warm\nMonths: Dec-Feb\nPrep ahead: Make stock\nPrep ahead days: 3\n- Leek"
    client.post("/api/meals/import", data=text, content_type="text/plain")
    soup = next(x for x in client.get("/api/meals").get_json() if x["name"] == "Soup")
    assert (soup["tags"], soup["months"], soup["prep_ahead"], soup["prep_ahead_days"]) == (["warm"], [1, 2, 12], "Make stock", 3)

    backup = client.get("/api/backup").get_data(as_text=True)
    client.delete(f"/api/meals/{mid}")
    assert client.post("/api/restore", data=backup, content_type="application/json").status_code == 200
    curry = next(x for x in client.get("/api/meals").get_json() if x["name"] == "Curry")
    assert (curry["tags"], curry["months"], curry["prep_ahead_days"]) == (["quick", "spicy"], [10, 11, 12], 2)


def test_v2_database_is_migrated_to_v3(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript(appmod.SCHEMA_V1)
    for table, col, ddl in appmod.V2_COLUMNS:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    for stmt in appmod.V2_STATEMENTS:
        conn.execute(stmt)
    conn.execute("INSERT INTO meals(name,category,leftovers,ingredients) VALUES ('Old stew','Stew',1,'Leek')")
    conn.execute("INSERT INTO plan(day,kind,meal_id,label) VALUES ('2030-01-07','meal',1,'Old stew')")
    conn.execute("INSERT INTO checked(scope,key) VALUES ('2030-01-07:1','leek')")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    monkeypatch.setitem(appmod.app.config, "DB_PATH", str(path))
    appmod.init_db()
    appmod.init_db()  # idempotent
    c = appmod.app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "fetch"
    m = c.get("/api/meals").get_json()[0]
    assert m["name"] == "Old stew" and m["tags"] == [] and m["months"] == [] and m["prep_ahead_days"] == 1
    assert plan_of(c, day(0), day(0))[day(0)]["name"] == "Old stew"
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plan_lunch", "aisle_map", "item_price", "stock"} <= names
    assert conn.execute("SELECT key, price FROM checked").fetchall() == [("leek", None)]


# ---------------------------------------------------------------- avoid list

def test_avoided_meals_are_never_planned_or_suggested(client):
    client.put("/api/settings", json={"avoid": ["peanut", "nuts"]})
    bad1 = mk(client, "Satay", ingredients=["200 g peanut butter", "Chicken"])
    bad2 = mk(client, "Granola", tags="nuts", ingredients=["Oats"])
    bad3 = mk(client, "Peanuts snack")
    good = [mk(client, f"Good {i}") for i in range(5)]
    client.post("/api/generate", json={"start": day(0), "end": day(4)})
    planned = {e["meal_id"] for e in plan_of(client, day(0), day(5)).values()}
    assert planned == set(good)
    for d in range(5):
        ids = {s["id"] for s in client.get(f"/api/suggest?day={day(d)}&n=6").get_json()}
        assert not ids & {bad1, bad2, bad3}
    flags = {m["name"]: m["avoided"] for m in client.get("/api/meals").get_json()}
    assert flags["Satay"] == "peanut" and flags["Granola"] == "nuts" and flags["Good 0"] == ""


def test_avoid_matches_whole_words_only(client):
    client.put("/api/settings", json={"avoid": ["nut"]})
    mk(client, "Doughnuts", ingredients=["Flour"])  # 'nut' is only part of 'doughnuts'
    mk(client, "Nut roast", ingredients=["Walnuts"])
    flags = {m["name"]: m["avoided"] for m in client.get("/api/meals").get_json()}
    assert flags == {"Doughnuts": "", "Nut roast": "nut"}


def test_planning_explains_when_everything_is_avoided(client):
    mk(client, "Satay", ingredients=["peanut butter"])
    client.put("/api/settings", json={"avoid": ["peanut"]})
    r = client.post("/api/generate", json={"start": day(0), "end": day(1)})
    assert r.status_code == 400 and "avoid list" in r.get_json()["error"]


# ---------------------------------------------------------------- tag rules and seasons

def test_tag_rule_is_preferred_on_those_weekdays_only(client):
    client.put("/api/settings", json={"rules": ["Mon-Fri = quick"]})
    quick = {mk(client, f"Quick {i}", tags="quick") for i in range(6)}
    for i in range(6):
        mk(client, f"Slow {i}")
    for seed in range(4):
        random.seed(seed)
        client.post("/api/generate", json={"start": day(0), "end": day(4), "replace": True})
        assert {e["meal_id"] for e in plan_of(client, day(0), day(4)).values()} <= quick
    # at the weekend anything goes (no assertion on which), but the rule must not leave a day empty
    client.post("/api/generate", json={"start": day(5), "end": day(6), "replace": True})
    assert len(plan_of(client, day(5), day(6))) == 2


def test_tag_rule_is_relaxed_when_no_meal_has_the_tag(client):
    client.put("/api/settings", json={"rules": ["Mon-Fri = quick"]})
    for i in range(7):
        mk(client, f"Slow {i}")
    r = client.post("/api/generate", json={"start": day(0), "end": day(4)}).get_json()
    assert r["planned"] == 5


def test_season_is_a_soft_preference(client):
    winter = {mk(client, f"Winter {i}", months="Dec-Feb") for i in range(5)}
    for i in range(5):
        mk(client, f"Summer {i}", months="Jun-Aug")
    january = [day(0), day(1), day(2), day(3), day(4)]  # 2030-01-07 ..
    client.post("/api/generate", json={"start": january[0], "end": january[-1]})
    assert {e["meal_id"] for e in plan_of(client, january[0], january[-1]).values()} == winter
    # more days than winter meals: the plan still gets filled
    r = client.post("/api/generate", json={"start": day(5), "end": day(9)}).get_json()
    assert r["planned"] == 5


# ---------------------------------------------------------------- suggestions

def test_suggestions_are_distinct_and_explained(client):
    ids = [mk(client, f"Meal {i}") for i in range(6)]
    s = client.get(f"/api/suggest?day={day(0)}&n=3").get_json()
    assert len(s) == 3 and len({x["id"] for x in s}) == 3
    assert all(x["id"] in ids and "never served" in x["why"] and x["repeat"] is False for x in s)


def test_suggestions_follow_the_gap_and_say_when_served(client):
    ids = [mk(client, f"Meal {i}") for i in range(5)]
    client.put(f"/api/plan/{day(-30)}", json={"kind": "meal", "meal_id": ids[0]})  # long enough ago
    client.put(f"/api/plan/{day(-2)}", json={"kind": "meal", "meal_id": ids[1]})   # too recent
    got = client.get(f"/api/suggest?day={day(0)}&n=6&today={day(0)}").get_json()
    fits = [x for x in got if not x["repeat"]]
    assert {x["id"] for x in fits} == {ids[0], ids[2], ids[3], ids[4]}  # the recent meal is not among the proper fits
    assert [x["id"] for x in got if x["repeat"]] == [ids[1]] and got[-1]["id"] == ids[1]  # only offered last, as a repeat
    assert "last served 30 days ago" in next(x for x in fits if x["id"] == ids[0])["why"]


def test_suggestions_skip_the_meal_already_planned_there(client):
    a, b = mk(client, "A"), mk(client, "B")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    s = client.get(f"/api/suggest?day={day(0)}&n=3").get_json()
    assert [x["id"] for x in s] == [b]


def test_suggestions_use_weekday_tag_and_season_rules(client):
    client.put("/api/settings", json={"rules": ["Mon = quick"]})
    quick = mk(client, "Quick one", tags="quick", months="Jan", weekdays="mon", weight=2)
    for i in range(4):
        mk(client, f"Other {i}")
    s = client.get(f"/api/suggest?day={day(0)}&n=1").get_json()[0]  # a Monday in January
    assert s["id"] == quick
    assert {"a favourite", "suits Mondays", "in season", "quick meal for Mondays"} <= set(s["why"])


def test_suggestions_with_no_meals_or_too_few(client):
    assert client.get(f"/api/suggest?day={day(0)}").get_json() == []
    mk(client, "Only")
    assert len(client.get(f"/api/suggest?day={day(0)}&n=5").get_json()) == 1
    assert client.get("/api/suggest?day=oops").status_code == 400
