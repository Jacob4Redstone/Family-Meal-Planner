from datetime import date, timedelta

from helpers import day, mk, plan_of


def lunch(c, d, **body):
    return c.put(f"/api/lunch/{d}", json=body)


def extras(c, start, end):
    return c.get(f"/api/plan/extras?start={start}&end={end}").get_json()


# ---------------------------------------------------------------- lunch slot

def test_lunch_can_be_a_meal_leftovers_or_a_note(client):
    a = mk(client, "Soup")
    assert lunch(client, day(0), kind="meal", meal_id=a).status_code == 200
    assert lunch(client, day(1), kind="leftover", meal_id=a).status_code == 200
    assert lunch(client, day(2), kind="custom", label="Canteen").status_code == 200
    got = extras(client, day(0), day(3))["lunch"]
    assert {d: (e["kind"], e["name"]) for d, e in got.items()} == {
        day(0): ("meal", "Soup"), day(1): ("leftover", "Soup"), day(2): ("custom", "Canteen")}
    assert lunch(client, day(0), kind="clear").status_code == 200
    assert day(0) not in extras(client, day(0), day(3))["lunch"]
    assert lunch(client, day(0), kind="meal", meal_id=999).status_code == 400
    assert lunch(client, "oops", kind="clear").status_code == 400


def test_lunch_is_not_touched_by_planning_swapping_or_undo(client):
    for i in range(8):
        mk(client, f"M{i}")
    mine = mk(client, "My lunch")
    lunch(client, day(2), kind="meal", meal_id=mine)
    client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True})
    client.post("/api/plan/swap", json={"a": day(1), "b": day(2)})
    client.post("/api/generate", json={"start": day(0), "end": day(6), "replace": True})
    client.post("/api/undo")
    assert extras(client, day(0), day(6))["lunch"][day(2)]["name"] == "My lunch"
    # and a lunch is not counted by the dinner gap rules: the same meal can still be planned for dinner
    dinner_ids = {e["meal_id"] for e in plan_of(client, day(0), day(6)).values()}
    assert mine in dinner_ids or len(dinner_ids) == 7


def test_lunch_counts_for_shopping_served_counts_and_the_feed(client):
    a = mk(client, "Soup", ingredients=["2 leeks", "1 l stock"])
    b = mk(client, "Stew", ingredients=["1 l stock", "Beef"])
    lunch(client, day(0), kind="meal", meal_id=a)
    lunch(client, day(1), kind="leftover", meal_id=a)   # leftovers add nothing
    lunch(client, day(2), kind="custom", label="Canteen")  # neither do notes
    client.put(f"/api/plan/{day(3)}", json={"kind": "meal", "meal_id": b})
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    assert {i["name"]: i["amount"] for i in s["items"]} == {"Leeks": "2", "Stock": "2 l", "Beef": ""}
    assert s["meals"] == ["Soup", "Stew"]

    today = date.today()
    lunch(client, today.isoformat(), kind="meal", meal_id=a)
    client.put(f"/api/plan/{today.isoformat()}", json={"kind": "meal", "meal_id": a})
    soup = next(m for m in client.get(f"/api/meals?today={today.isoformat()}").get_json() if m["name"] == "Soup")
    assert soup["times_served"] == 2 and soup["last_served"] == today.isoformat()  # lunch and dinner

    ics = client.get("/calendar.ics").get_data(as_text=True)
    assert f"UID:{today.isoformat()}-lunch@mealplanner" in ics and "SUMMARY:Lunch: Soup" in ics


def test_lunch_follows_meal_edits_and_deletes(client):
    a = mk(client, "Soup", ingredients=["Leek"])
    lunch(client, day(0), kind="meal", meal_id=a)
    client.put(f"/api/meals/{a}", json={"name": "Leek soup", "ingredients": "Leek"})
    assert extras(client, day(0), day(0))["lunch"][day(0)]["name"] == "Leek soup"
    client.delete(f"/api/meals/{a}")
    assert extras(client, day(0), day(0))["lunch"][day(0)]["name"] == "Leek soup"  # name survives
    s = client.get(f"/api/shopping?week={day(0)}").get_json()
    assert [i["name"] for i in s["items"]] == ["Leek"]  # and so do the ingredients


def test_backup_restore_keeps_lunch_and_cooks(client):
    a = mk(client, "Soup")
    lunch(client, day(0), kind="meal", meal_id=a)
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": a, "cook": "Ben"})
    backup = client.get("/api/backup").get_data(as_text=True)
    lunch(client, day(0), kind="clear")
    client.put(f"/api/plan/{day(1)}", json={"kind": "clear"})
    r = client.post("/api/restore", data=backup, content_type="application/json").get_json()
    assert r["days"] == 2
    e = extras(client, day(0), day(1))
    assert e["lunch"][day(0)]["name"] == "Soup" and e["cook"] == {day(1): "Ben"}


# ---------------------------------------------------------------- cooks

def test_cook_rule_and_override(client):
    a = mk(client, "Soup")
    client.put("/api/settings", json={"cooks": ["Mon-Wed = Anna", "Thu = Ben"]})
    c = extras(client, day(0), day(6))["cook"]
    assert c == {day(0): "Anna", day(1): "Anna", day(2): "Anna", day(3): "Ben"}
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": a, "cook": "Clara"})
    e = extras(client, day(0), day(6))
    assert e["cook"][day(1)] == "Clara" and e["override"] == {day(1): "Clara"}  # the dialog needs to tell them apart
    # editing the day without mentioning the cook keeps the override
    b = mk(client, "Stew")
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": b})
    assert extras(client, day(0), day(6))["cook"][day(1)] == "Clara"
    # an empty string removes it again, and the weekday rule applies once more
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": b, "cook": ""})
    assert extras(client, day(0), day(6))["cook"][day(1)] == "Anna"


def test_cook_override_survives_replanning_swap_and_undo(client):
    for i in range(6):
        mk(client, f"M{i}")
    client.post("/api/generate", json={"start": day(0), "end": day(6)})
    a = next(iter(plan_of(client, day(0), day(0)).values()))["meal_id"]
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a, "cook": "Ben"})
    client.post("/api/generate", json={"start": day(0), "end": day(0), "replace": True, "reroll": True})
    assert extras(client, day(0), day(0))["cook"] == {day(0): "Ben"}
    client.post("/api/plan/swap", json={"a": day(0), "b": day(3)})
    assert extras(client, day(0), day(3))["cook"] == {day(3): "Ben"}  # the cook moves with the meal
    client.post("/api/generate", json={"start": day(3), "end": day(3), "replace": True, "reroll": True})
    assert client.post("/api/undo").status_code == 200
    assert extras(client, day(0), day(3))["cook"] == {day(3): "Ben"}


# ---------------------------------------------------------------- prepare-ahead reminders

def test_prep_reminder_appears_the_chosen_days_before(client):
    a = mk(client, "Chicken", prep_ahead="Marinate the chicken", prep_ahead_days=2)
    b = mk(client, "Beans", prep_ahead="Soak the beans")
    client.put(f"/api/plan/{day(4)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(5)}", json={"kind": "meal", "meal_id": b})
    lunch(client, day(6), kind="meal", meal_id=b)
    prep = extras(client, day(0), day(6))["prep"]
    assert prep[day(2)] == [{"text": "Marinate the chicken", "meal": "Chicken", "for_day": day(4), "slot": "dinner"}]
    assert prep[day(4)] == [{"text": "Soak the beans", "meal": "Beans", "for_day": day(5), "slot": "dinner"}]
    assert prep[day(5)] == [{"text": "Soak the beans", "meal": "Beans", "for_day": day(6), "slot": "lunch"}]
    assert day(3) not in prep  # exactly on the chosen day, not every day in between


def test_prep_reminder_for_the_first_days_of_the_next_range(client):
    a = mk(client, "Chicken", prep_ahead="Marinate", prep_ahead_days=3)
    client.put(f"/api/plan/{day(8)}", json={"kind": "meal", "meal_id": a})
    assert extras(client, day(0), day(4))["prep"] == {}                      # due on day 5, outside this range
    assert list(extras(client, day(5), day(6))["prep"]) == [day(5)]          # a meal 3 days later is still found
    leftovers = mk(client, "Leftover meal", prep_ahead="x")
    client.put(f"/api/plan/{day(1)}", json={"kind": "leftover", "meal_id": leftovers})
    client.put(f"/api/plan/{day(2)}", json={"kind": "custom", "label": "Out"})
    assert extras(client, day(0), day(1))["prep"] == {}                      # leftovers and notes never remind


def test_extras_range_is_validated(client):
    assert client.get("/api/plan/extras?start=oops&end=2030-01-01").status_code == 400
    assert client.get(f"/api/plan/extras?start={day(0)}&end={day(500)}").status_code == 400
    assert extras(client, day(0), day(0)) == {"lunch": {}, "cook": {}, "override": {}, "prep": {}}
    assert (date.fromisoformat(day(500)) - date.fromisoformat(day(0))) > timedelta(400)
