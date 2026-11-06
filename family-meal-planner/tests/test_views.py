from helpers import day, mk


def today(c, d):
    return c.get(f"/api/today?today={d}").get_json()["days"]


def stats(c, month, d):
    return c.get(f"/api/stats?month={month}&today={d}").get_json()


# ---------------------------------------------------------------- Today

def test_today_and_tomorrow_with_meal_details(client):
    a = mk(client, "Chicken curry", category="Curry", tags="quick", prep_minutes=40, notes="Use the big pot",
           url="https://example.com/curry", ingredients=["500 g chicken", "1 onion"], servings=4)
    b = mk(client, "Soup", leftovers=True)
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": b})
    client.put(f"/api/lunch/{day(0)}", json={"kind": "custom", "label": "Canteen"})
    t, tm = today(client, day(0))
    assert (t["label"], t["day"], tm["label"], tm["day"]) == ("today", day(0), "tomorrow", day(1))
    assert t["dinner"]["name"] == "Chicken curry" and t["dinner"]["kind"] == "meal"
    meal = t["dinner"]["meal"]
    assert (meal["notes"], meal["url"], meal["prep_minutes"], meal["tags"], meal["servings"]) == (
        "Use the big pot", "https://example.com/curry", 40, ["quick"], 4)
    assert meal["ingredients"] == ["500 g chicken", "1 onion"]
    assert t["lunch"] == {"kind": "custom", "name": "Canteen", "meal": None}
    assert tm["dinner"]["name"] == "Soup" and tm["lunch"] is None


def test_today_shows_leftovers_with_their_source_meal_and_empty_days(client):
    a = mk(client, "Roast", leftovers=True, notes="Reheat gently")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})  # day(1) = leftovers
    t, tm = today(client, day(1))
    assert t["dinner"]["kind"] == "leftover" and t["dinner"]["name"] == "Roast"
    assert t["dinner"]["meal"]["notes"] == "Reheat gently"
    assert tm["dinner"] is None and tm["lunch"] is None and tm["cook"] == "" and tm["prep"] == []


def test_today_cooks_and_prep_reminders(client):
    client.put("/api/settings", json={"cooks": ["Mon-Sun = Anna"]})
    a = mk(client, "Chicken", prep_ahead="Marinate the chicken", prep_ahead_days=1)
    b = mk(client, "Beans", prep_ahead="Soak the beans", prep_ahead_days=2)
    client.put(f"/api/plan/{day(1)}", json={"kind": "meal", "meal_id": a, "cook": "Ben"})   # reminder due today
    client.put(f"/api/plan/{day(2)}", json={"kind": "meal", "meal_id": b})                   # due today (2 days before)
    client.put(f"/api/plan/{day(3)}", json={"kind": "meal", "meal_id": b})                   # due tomorrow
    t, tm = today(client, day(0))
    assert [p["text"] for p in t["prep"]] == ["Marinate the chicken", "Soak the beans"] and t["cook"] == "Anna"
    assert [(p["text"], p["for_day"]) for p in tm["prep"]] == [("Soak the beans", day(3))] and tm["cook"] == "Ben"


def test_today_uses_the_given_date_and_validates_it(client):
    a = mk(client, "Soup")
    client.put(f"/api/plan/{day(5)}", json={"kind": "meal", "meal_id": a})
    assert today(client, day(5))[0]["dinner"]["name"] == "Soup"
    assert today(client, day(4))[1]["dinner"]["name"] == "Soup"
    assert client.get("/api/today?today=oops").status_code == 400
    assert len(client.get("/api/today").get_json()["days"]) == 2  # defaults to the server date


def test_today_survives_a_deleted_meal(client):
    a = mk(client, "Soup", notes="x")
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.delete(f"/api/meals/{a}")
    d = today(client, day(0))[0]["dinner"]
    assert d["name"] == "Soup" and d["meal"] is None


# ---------------------------------------------------------------- History

def plan_meals(c):
    a = mk(c, "Pasta", category="Pasta")
    b = mk(c, "Fish", category="Fish")
    s = mk(c, "Soup", category="Soup")
    never = mk(c, "Never cooked", category="Misc")
    return a, b, s, never


def test_stats_counts_dinner_and_lunch_up_to_today(client):
    a, b, s, never = plan_meals(client)
    for off, m in ((0, a), (1, b), (2, a), (4, s)):
        client.put(f"/api/plan/{day(off)}", json={"kind": "meal", "meal_id": m})
    client.put(f"/api/lunch/{day(1)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/lunch/{day(2)}", json={"kind": "leftover", "meal_id": a})  # leftovers are not a new meal
    client.put(f"/api/plan/{day(3)}", json={"kind": "custom", "label": "Out"})
    st = stats(client, "2030-01", day(2))  # today = Jan 9: the Jan 11 soup is still in the future
    assert st["month"] == "2030-01" and st["start"] == "2030-01-01" and st["end"] == "2030-01-31"
    assert [(m["name"], m["count"]) for m in st["by_meal"]] == [("Pasta", 3), ("Fish", 1)]
    assert st["by_category"] == [{"category": "Pasta", "count": 3}, {"category": "Fish", "count": 1}]
    assert st["totals"] == {"meals_eaten": 4, "days_eaten": 3, "distinct_meals": 2, "repeats": 2}
    by_day = {d["day"]: d for d in st["days"]}
    assert by_day[day(1)]["dinner"]["name"] == "Fish" and by_day[day(1)]["lunch"]["name"] == "Pasta"
    assert by_day[day(2)]["lunch"]["kind"] == "leftover" and by_day[day(3)]["dinner"]["kind"] == "custom"
    assert by_day[day(4)]["future"] is True and by_day[day(0)]["future"] is False
    assert "Soup" not in [m["name"] for m in st["by_meal"]]


def test_stats_forgotten_and_never_served(client):
    a, b, s, never = plan_meals(client)
    client.put(f"/api/plan/{day(-100)}", json={"kind": "meal", "meal_id": a})
    client.put(f"/api/plan/{day(-10)}", json={"kind": "meal", "meal_id": b})
    client.put(f"/api/plan/{day(-40)}", json={"kind": "meal", "meal_id": s})
    client.put(f"/api/plan/{day(30)}", json={"kind": "meal", "meal_id": never})  # planned, not served yet
    st = stats(client, "2030-01", day(0))
    assert [(f["name"], f["days_ago"]) for f in st["forgotten"]] == [("Pasta", 100), ("Soup", 40), ("Fish", 10)]
    assert st["never"] == ["Never cooked"]


def test_stats_months_and_validation(client):
    a, *_ = plan_meals(client)
    client.put("/api/plan/2030-02-28", json={"kind": "meal", "meal_id": a})
    client.put("/api/plan/2030-03-01", json={"kind": "meal", "meal_id": a})
    feb = stats(client, "2030-02", "2030-12-31")
    assert feb["end"] == "2030-02-28" and feb["totals"]["meals_eaten"] == 1
    assert stats(client, "2030-03", "2030-12-31")["totals"]["meals_eaten"] == 1
    assert client.get("/api/stats?month=2030-13").status_code == 400
    assert client.get("/api/stats?month=oops").status_code == 400
    default = client.get("/api/stats?today=2030-02-10").get_json()
    assert default["month"] == "2030-02"


def test_stats_keep_counting_a_deleted_meal_by_name(client):
    a, *_ = plan_meals(client)
    client.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    client.delete(f"/api/meals/{a}")
    st = stats(client, "2030-01", day(5))
    assert [(m["name"], m["count"]) for m in st["by_meal"]] == [("Pasta", 1)]
    assert st["forgotten"] == []  # a deleted meal can't be 'forgotten'
