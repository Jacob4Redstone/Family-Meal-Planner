import pytest
from helpers import appmod, day, mk

import ingredients as ing


@pytest.mark.parametrize("line,key,name", [
    ("2 red onions", "onion", "Onion"),
    ("1 yellow onion, diced", "onion", "Onion"),
    ("4 scallions", "spring onion", "Spring onion"),
    ("1 bunch cilantro", "coriander", "Coriander"),
    ("500 g ground beef", "minced beef", "Minced beef"),
    ("1 zucchini", "courgette", "Courgette"),
    ("2 onions", "onion", "Onions"),  # no synonym: unchanged
])
def test_builtin_synonyms(line, key, name):
    k, n, _q, _u = ing.parse_ingredient(line)
    assert (k, n) == (key, name)


def test_user_synonyms_add_override_and_validate():
    syn = ing.build_synonyms(["Cherry tomatoes = tomato", "red onion = red onion"])
    assert ing.parse_ingredient("200 g cherry tomatoes", synonyms=syn)[:2] == ("tomato", "Tomato")
    assert ing.parse_ingredient("1 red onion", synonyms=syn)[0] == "red onion"  # the built-in merge is switched off
    with pytest.raises(ValueError):
        ing.build_synonyms(["no equals sign here"])


@pytest.mark.parametrize("line,key", [
    ("1 large onion", "onion"),
    ("3 small potatoes", "potato"),
    ("2 extra large eggs", "egg"),
    ("Medium carrot", "carrot"),
    ("1 whole chicken", "whole chicken"),  # 'whole' changes the product, so it stays
    ("fresh basil", "fresh basil"),
])
def test_size_words_are_dropped(line, key):
    assert ing.parse_ingredient(line)[0] == key


def test_recipe_symbol_is_reported():
    assert ing.parse_ingredient_full("2 dl cream")[3:] == ("ml", "dl")
    assert ing.parse_ingredient_full("1.5 kg flour")[3:] == ("g", "kg")
    assert ing.parse_ingredient_full("Onion")[2:] == (None, "", "")


@pytest.mark.parametrize("q,unit,system,symbol,expected", [
    (350, "ml", "recipe", "dl", "3.5 dl"),        # the recipes wrote dl: keep dl
    (50, "ml", "recipe", "dl", "50 ml"),          # less than 1 dl: plain ml
    (1300, "ml", "recipe", "dl", "1.3 l"),        # from a litre up: litres
    (45, "ml", "recipe", "cl", "4.5 cl"),
    (750, "ml", "recipe", "l", "750 ml"),         # never '0.75 l'
    (1500, "g", "recipe", "g", "1.5 kg"),
    (200, "g", "metric", "", "200 g"),
    (500, "g", "imperial", "", "1.1 lb"),
    (100, "g", "imperial", "", "3.53 oz"),
    (250, "ml", "imperial", "", "1.04 cups"),
    (240, "ml", "imperial", "", "1 cup"),
    (30, "ml", "imperial", "", "1.01 fl oz"),
    (4, "clove", "imperial", "", "4 cloves"),
])
def test_display_units(q, unit, system, symbol, expected):
    assert ing.fmt_qty(q, unit, system, symbol) == expected


def shop(c, week_day=0, weeks=1):
    return {i["name"]: i["amount"] for i in c.get(f"/api/shopping?week={day(week_day)}&weeks={weeks}").get_json()["items"]}


def plan_two(c, a_lines, b_lines):
    a, b = mk(c, "A", ingredients=a_lines), mk(c, "B", ingredients=b_lines)
    c.put(f"/api/plan/{day(0)}", json={"kind": "meal", "meal_id": a})
    c.put(f"/api/plan/{day(2)}", json={"kind": "meal", "meal_id": b})


def test_shopping_keeps_the_recipe_unit(client):
    plan_two(client, ["2 dl cream", "1 kg potatoes"], ["1,5 dl cream", "500 g potatoes"])
    items = shop(client)
    assert items["Cream"] == "3.5 dl" and items["Potatoes"] == "1.5 kg"


def test_shopping_uses_the_majority_recipe_unit(client):
    plan_two(client, ["2 dl milk", "3 dl oats"], ["1 dl milk", "5 cl syrup"])
    assert shop(client)["Milk"] == "3 dl"
    plan_two_more = mk(client, "C", ingredients=["300 ml milk"])
    client.put(f"/api/plan/{day(4)}", json={"kind": "meal", "meal_id": plan_two_more})
    assert shop(client)["Milk"] == "6 dl"  # dl (2 recipes) beats ml (1 recipe)


def test_shopping_synonyms_merge_and_user_lines(client):
    plan_two(client, ["2 red onions", "200 g cherry tomatoes"], ["1 yellow onion", "1 tomato"])
    items = shop(client)
    assert items["Onion"] == "3" and "Red onions" not in items
    client.put("/api/settings", json={"synonyms": ["cherry tomato = tomato"]})
    assert shop(client)["Tomato"] == "200 g + 1"


def test_metric_system_merges_imperial_units(client):
    plan_two(client, ["1 cup milk", "1 lb beef"], ["200 ml milk", "200 g beef"])
    assert shop(client)["Milk"] == "1 cup + 200 ml"  # recipe system keeps them apart
    client.put("/api/settings", json={"unit_system": "metric"})
    items = shop(client)
    assert items["Milk"] == "440 ml" and items["Beef"] == "653.59 g"


def test_imperial_system_converts_metric(client):
    plan_two(client, ["500 g flour", "250 ml milk"], ["100 g flour"])
    client.put("/api/settings", json={"unit_system": "imperial"})
    items = shop(client)
    assert items["Flour"] == "1.32 lb" and items["Milk"] == "1.04 cups"


def test_settings_validate_new_ingredient_options(client):
    assert client.put("/api/settings", json={"synonyms": ["oops"]}).status_code == 400
    s = client.put("/api/settings", json={"unit_system": "nonsense"}).get_json()
    assert s["unit_system"] == "recipe"
    s = client.put("/api/settings", json={"synonyms": "Red Onion = onion\n", "unit_system": "metric"}).get_json()
    assert s["synonyms"] == ["Red Onion = onion"] and s["unit_system"] == "metric"


def test_module_is_reexported_for_the_app():
    assert appmod.parse_ingredient is ing.parse_ingredient and appmod.fmt_qty is ing.fmt_qty
