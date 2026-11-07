import base64
import sqlite3
from datetime import date, timedelta

import pytest
from helpers import appmod, day, mk

import ingredients as ing


def planned(c, ingredients, d=0):
    a = mk(c, f"Meal {d}-{abs(hash(tuple(ingredients))) % 1000}", ingredients=ingredients)
    c.put(f"/api/plan/{day(d)}", json={"kind": "meal", "meal_id": a})
    return a


def lst(c, week=0, weeks=1):
    return c.get(f"/api/shopping?week={day(week)}&weeks={weeks}").get_json()


def item(s, name):
    return next(i for i in s["items"] if i["name"] == name)


# ---------------------------------------------------------------- aisles

@pytest.mark.parametrize("key,aisle", [
    ("onion", "Produce"), ("spring onion", "Produce"), ("green bean", "Produce"),
    ("kidney bean", "Pantry"), ("tomato sauce", "Pantry"), ("coconut milk", "Pantry"), ("peanut butter", "Pantry"),
    ("chicken stock", "Pantry"), ("chicken breast", "Meat & fish"), ("frozen pea", "Frozen"),
    ("red wine vinegar", "Pantry"), ("milk", "Dairy & eggs"), ("sourdough bread", "Bakery"), ("salt", "Spices & herbs"),
    ("unobtainium", "Other"),
])
def test_default_aisle_lookup(key, aisle):
    assert ing.lookup_aisle(key) == aisle


def test_your_own_aisle_wins_and_aisles_are_ordered():
    assert ing.lookup_aisle("onion", {"onion": "Veg corner"}) == "Veg corner"
    present = {"Other", "Pantry", "Zoo", "Produce", "Bakery"}
    assert ing.order_aisles(present) == ["Produce", "Bakery", "Pantry", "Zoo", "Other"]
    assert ing.order_aisles(present, ["Pantry", "Produce"]) == ["Pantry", "Produce", "Bakery", "Zoo", "Other"]
    assert ing.order_aisles(present, ["Other"])[0] == "Other"


def test_shopping_items_carry_their_aisle_and_the_groups_follow_the_order_setting(client):
    planned(client, ["2 onions", "500 g beef", "1 l milk", "Mystery stuff", "1 loaf bread"])
    s = lst(client)
    assert {i["name"]: i["aisle"] for i in s["items"]} == {
        "Onions": "Produce", "Beef": "Meat & fish", "Milk": "Dairy & eggs", "Mystery stuff": "Other", "Loaf bread": "Bakery"}
    assert s["aisles"] == ["Produce", "Bakery", "Meat & fish", "Dairy & eggs", "Other"]
    client.put("/api/settings", json={"aisle_order": ["Dairy & eggs", "Meat & fish"]})
    assert lst(client)["aisles"] == ["Dairy & eggs", "Meat & fish", "Produce", "Bakery", "Other"]


def test_aisle_choice_is_remembered_and_can_be_reset(client):
    planned(client, ["2 onions"])
    s = lst(client)
    r = client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "aisle": "Veg corner"})
    assert r.status_code == 200
    assert item(lst(client), "Onions")["aisle"] == "Veg corner"
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "aisle": ""})
    assert item(lst(client), "Onions")["aisle"] == "Produce"
    assert client.put("/api/shopping/item", json={"aisle": "x"}).status_code == 400


# ---------------------------------------------------------------- prices

def test_price_ticks_the_item_remembers_a_hint_and_sums_up(client):
    planned(client, ["2 onions", "500 g beef"])
    s = lst(client)
    assert item(s, "Beef")["price"] is None and item(s, "Beef")["price_hint"] is None and s["total"] == 0
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "beef", "price": "4.5"})
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "price": 1.25})
    s = lst(client)
    assert item(s, "Beef")["checked"] is True and item(s, "Beef")["price"] == 4.5   # paid = bought
    assert s["total"] == 5.75
    # next week the last price is offered as a hint
    planned(client, ["500 g beef"], d=8)
    nxt = lst(client, week=8)
    assert item(nxt, "Beef")["price"] is None and item(nxt, "Beef")["price_hint"] == 4.5 and nxt["total"] == 0
    # clearing the price keeps the tick; unticking removes the price
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "beef", "price": None})
    assert item(lst(client), "Beef")["checked"] is True and item(lst(client), "Beef")["price"] is None
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "price": 2})
    client.put("/api/shopping/check", json={"scope": s["scope"], "key": "onion", "checked": False})
    assert item(lst(client), "Onions")["price"] is None


@pytest.mark.parametrize("bad", ["abc", -1, "nan", 10 ** 7])
def test_bad_prices_are_rejected(client, bad):
    planned(client, ["2 onions"])
    s = lst(client)
    assert client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "price": bad}).status_code == 400


# ---------------------------------------------------------------- stock

def test_in_stock_items_leave_the_list_until_the_date(client):
    planned(client, ["500 g rice", "2 onions"])
    s = lst(client)
    r = client.put("/api/shopping/item", json={"key": "rice", "stock_days": 30, "today": day(0)})
    assert r.status_code == 200
    s = lst(client)
    assert [i["name"] for i in s["items"]] == ["Onions"]
    assert s["in_stock"] == [{"key": "rice", "name": "Rice", "until": day(30)}]
    # a list that reaches past the date shows the item again
    planned(client, ["500 g rice"], d=40)
    assert "Rice" in [i["name"] for i in lst(client, week=36, weeks=2)["items"]]
    # "need it again"
    client.put("/api/shopping/item", json={"key": "rice", "stock_days": 0})
    s = lst(client)
    assert "Rice" in [i["name"] for i in s["items"]] and s["in_stock"] == []


def test_stock_that_ends_inside_the_week_does_not_hide_the_item(client):
    planned(client, ["500 g rice"])
    client.put("/api/shopping/item", json={"key": "rice", "stock_days": 3, "today": day(0)})  # until day(3); the week ends day(6)
    s = lst(client)
    assert [i["name"] for i in s["items"]] == ["Rice"] and s["in_stock"] == []


def test_expired_stock_is_pruned(client):
    conn = sqlite3.connect(appmod.app.config["DB_PATH"])
    conn.execute("INSERT INTO stock(key, until) VALUES ('old', ?), ('fresh', ?)",
                 ((date.today() - timedelta(2)).isoformat(), (date.today() + timedelta(2)).isoformat()))
    conn.commit()
    conn.close()
    client.put("/api/shopping/item", json={"key": "milk", "aisle": "Dairy & eggs"})  # any write prunes
    conn = sqlite3.connect(appmod.app.config["DB_PATH"])
    assert [r[0] for r in conn.execute("SELECT key FROM stock")] == ["fresh"]
    conn.close()


def test_backup_restore_keeps_aisles_prices_and_stock_but_not_the_share_link(client):
    planned(client, ["2 onions", "500 g rice"])
    s = lst(client)
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "aisle": "Veg corner", "price": 3.2})
    client.put("/api/shopping/item", json={"key": "rice", "stock_days": 20, "today": day(0)})
    token = client.post("/api/share").get_json()["token"]
    backup = client.get("/api/backup").get_data(as_text=True)
    client.put("/api/shopping/item", json={"scope": s["scope"], "key": "onion", "aisle": ""})
    client.put("/api/shopping/item", json={"key": "rice", "stock_days": 0})
    assert client.post("/api/restore", data=backup, content_type="application/json").status_code == 200
    after = lst(client)
    assert item(after, "Onions")["aisle"] == "Veg corner" and item(after, "Onions")["price"] == 3.2
    assert after["in_stock"][0]["key"] == "rice"
    assert client.get("/api/share").get_json()["token"] == token  # restoring does not break the link


# ---------------------------------------------------------------- share link

def hdr():
    return {"X-Requested-With": "fetch"}


def test_share_link_lifecycle(client):
    assert client.get("/api/share").get_json() == {"token": None, "path": None}
    first = client.post("/api/share").get_json()
    assert first["path"] == f"/s/{first['token']}" and len(first["token"]) >= 20
    assert client.get("/api/share").get_json()["token"] == first["token"]
    second = client.post("/api/share").get_json()
    assert second["token"] != first["token"]
    assert client.get(f"/s/{first['token']}").status_code == 404          # the old link stops working
    assert client.get(f"/s/{second['token']}").status_code == 200
    client.delete("/api/share")
    assert client.get(f"/s/{second['token']}").status_code == 404
    assert client.get("/api/share").get_json()["token"] is None


def test_share_link_can_view_tick_and_add_extras_and_nothing_else(client):
    planned(client, ["2 onions", "500 g beef"])
    t = client.post("/api/share").get_json()["token"]
    anon = appmod.app.test_client()          # no custom header yet: a plain visitor
    page = anon.get(f"/s/{t}")
    assert page.status_code == 200 and b"<html" in page.data
    assert page.headers["Referrer-Policy"] == "no-referrer" and "noindex" in page.headers["X-Robots-Tag"]
    assert page.headers["Cache-Control"] == "no-store"

    s = anon.get(f"/s/{t}/api/shopping?week={day(0)}").get_json()
    assert s == lst(client)                  # the same list as the owner sees
    assert anon.put(f"/s/{t}/api/check", json={"scope": s["scope"], "key": "onion", "checked": True}).status_code == 403
    anon.environ_base["HTTP_X_REQUESTED_WITH"] = "fetch"
    assert anon.put(f"/s/{t}/api/check", json={"scope": s["scope"], "key": "onion", "checked": True}).status_code == 200
    eid = anon.post(f"/s/{t}/api/extras", json={"scope": s["scope"], "name": "Soap"}).get_json()["id"]
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    assert item(s, "Onions")["checked"] is True and s["extras"] == [{"id": eid, "name": "Soap", "checked": False}]
    assert anon.delete(f"/s/{t}/api/extras/{eid}").status_code == 200
    assert client.get(f"/api/shopping?week={day(0)}").get_json()["extras"] == []

    # nothing else is reachable through the link
    for path in (f"/s/{t}/api/meals", f"/s/{t}/api/plan?start={day(0)}&end={day(1)}", f"/s/{t}/api/settings",
                 f"/s/{t}/api/backup", f"/s/{t}/api/share"):
        assert anon.get(path).status_code == 404, path
    assert anon.put(f"/s/{t}/api/settings", json={}).status_code in (404, 405)


def test_wrong_tokens_get_404_everywhere(client):
    planned(client, ["2 onions"])
    t = client.post("/api/share").get_json()["token"]
    for bad in ("nope", t[:-1] + ("A" if t[-1] != "A" else "B"), t + "x"):
        assert client.get(f"/s/{bad}").status_code == 404
        assert client.get(f"/s/{bad}/api/shopping?week={day(0)}").status_code == 404
        assert client.put(f"/s/{bad}/api/check", json={"scope": "x", "key": "y", "checked": True}).status_code == 404
        assert client.post(f"/s/{bad}/api/extras", json={"scope": "x", "name": "y"}).status_code == 404
        assert client.delete(f"/s/{bad}/api/extras/1").status_code == 404


def test_share_link_works_without_the_password_but_the_rest_still_needs_it(client, monkeypatch):
    planned(client, ["2 onions"])
    t = client.post("/api/share").get_json()["token"]
    monkeypatch.setattr(appmod, "AUTH_PASSWORD", "secret")
    anon = appmod.app.test_client()
    assert anon.get(f"/s/{t}/api/shopping?week={day(0)}").status_code == 200
    assert anon.get(f"/s/{t}").status_code == 200
    assert anon.get("/api/shopping?week=" + day(0)).status_code == 401
    assert anon.get("/api/meals").status_code == 401
    assert anon.get("/api/share").status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"u:secret").decode()}
    assert anon.get("/api/share", headers=good).status_code == 200
    assert anon.get(f"/s/bad-token/api/shopping?week={day(0)}").status_code == 404  # still validated
