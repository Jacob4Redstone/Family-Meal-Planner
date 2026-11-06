# Family meal planner

Meals → automatic monthly planning → leftovers → weekly shopping lists.

Tabs: **Today** (what is for dinner, with notes, recipe link and reminders), **Calendar**, **Shopping list**, **Meals**, **History** and **Settings**.

## Run with Docker
    docker compose up -d --build
    # open http://<server>:8080

Data lives in the `mealplanner-data` volume (one SQLite file, Docker names it `<folder>_mealplanner-data`, for example `family-meal-planner_mealplanner-data`).

Optional settings go in a `.env` file next to `docker-compose.yml` (or the environment):

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_PASSWORD` | empty | If set, the whole app asks for this password (HTTP basic auth, any user name). Use it behind HTTPS. |
| `BIND` | `0.0.0.0` | Address to publish on. Use `127.0.0.1` when a reverse proxy runs on the same machine. |
| `PORT` | `8080` | Published port. |
| `GAP_DAYS` | `21` | A meal is not repeated within this many days. |
| `ENABLE_URL_IMPORT` | off | Set to `1` to show "Fill in from a recipe link" in the meal form. The server then downloads the page you give it (private and local addresses are refused). |

### Backup
Use **Settings → Download backup** (a JSON file with meals, plan, lunches, settings, shopping ticks, aisles, prices and stock; the share link is deliberately not part of it) and **Restore from file** to move or recover everything. For a raw database copy while the app runs:

    docker compose exec mealplanner python -c "import sqlite3; s=sqlite3.connect('/data/mealplanner.db'); s.backup(sqlite3.connect('/data/backup.db'))"
    docker compose cp mealplanner:/data/backup.db ./backup.db

## Proxmox VE LXC (community-scripts menu)
`ct/mealplanner.sh` + `install/mealplanner-install.sh` use the same engine as the community-scripts (Default / Advanced settings menu).
1. Edit `REPO_URL` in `install/mealplanner-install.sh` if you use your own fork, and push.
2. On the Proxmox host shell:

       export COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/Jacob4Redstone/Family-Meal-Planner/main/family-meal-planner
       bash -c "$(curl -fsSL $COMMUNITY_SCRIPTS_URL/ct/mealplanner.sh)"

   `COMMUNITY_SCRIPTS_URL` makes the engine fetch `install/mealplanner-install.sh` from this repo instead of community-scripts'.
3. Update later: run `update` inside the container.
4. Settings such as `AUTH_PASSWORD` go into the service: `systemctl edit mealplanner`, add `[Service]` and `Environment=AUTH_PASSWORD=...`, then `systemctl restart mealplanner`.

`proxmox/standalone-installer.sh` is a plain fallback with no menu and no dependency on the community-scripts engine.

## How planning works
- The pool is every meal you have. A meal is never used twice within 21 days (`GAP_DAYS`), counting days before and after the range, so adjacent months stay consistent.
- **Leftovers:** a meal that makes leftovers reserves the next 1–3 days (you choose per meal) as "Leftovers from …". Those days add nothing to the shopping list. A reserved day is never overwritten: if the next day is already planned, you get a message and the leftover day is skipped.
- **Variety:** avoids the same category two days running when it can.
- **Weekday rules:** a meal can be limited to certain weekdays (for example a slow roast only on Sunday).
- **Favourites:** meals marked Favourite are picked about twice as often, Rarely about a third as often.
- **Less waste:** meals that share ingredients with other meals of the same week are slightly preferred.
- **Weekly limits** (Settings): for example `Pasta = 2` avoids a third pasta meal in one week.
- **Tags and tag rules:** give meals tags (`quick`, `vegetarian`, `kid-friendly`) and add a rule such as `Mon-Fri = quick` in Settings. Planning then prefers `quick` meals on those weekdays.
- **Seasons:** a meal can be limited to months (Spring/Summer/Autumn/Winter buttons, or pick months). Out-of-season meals are only used when nothing else fits.
- **Never plan** (Settings): a list of words for allergies and dislikes, for example `peanut`. A meal is excluded when the word is one of its tags or appears as a whole word in its name or ingredients. It is a hard rule for planning and suggestions (days you plan by hand are not affected), and excluded meals are marked in the Meals list.
- **Suggestions:** in a day's dialog, "Suggest meals" shows 3 meals that fit that day under all the rules above, with the reason ("last served 34 days ago", "quick meal for Mondays"). When too few meals fit, it says so ("repeats a recent meal").
- **Fixed days** (Settings): for example `Friday = Takeaway` puts that note on every Friday that planning fills.
- **Pinned days:** pin a day in its dialog and planning ("Replace" or "Pick another") never changes it.
- If you have too few meals to honour the gap, the one served longest ago is reused and you are told.
- **Undo:** "Undo last planning" restores the days as they were before the last plan or re-roll. It is offered for one hour, and only until you change the plan in some other way (editing a day, swapping, deleting a meal), so it can never wipe later edits. "Replace" asks for confirmation first.
- Drag a day onto another to swap them (or use "Swap with another day" in the dialog). Leftover days move with their meal; if there is no room for them you are told. Clearing a leftover day also frees the leftover days after it, and "Fill" never re-adds a leftover day you cleared (only "Replace" does).
- Changing a meal's leftover setting only affects days from today on; the past stays as it was.

## Lunch, cooks and reminders
- **Lunch:** every day dialog has a lunch section (a meal, leftovers or a note). Lunch is filled in by hand: automatic planning, the repeat gap, swapping and undo never touch it. Lunch meals count for the shopping list, the calendar feed, "last served" and History.
- **Who cooks** (Settings): for example `Mon-Fri = Anna` and `Sat, Sun = Ben`. You can choose another cook for a single day in its dialog; that choice moves with the meal when you swap days and survives re-planning.
- **Prepare ahead:** a meal can carry a reminder ("Marinate the chicken") shown 1, 2 or 3 days before it, on the calendar and on the Today page.

## Today and History
- **Today** shows today and tomorrow in large text: the dinner, lunch, cook, your notes, the recipe link, the ingredients, and any "prepare ahead" reminder that is due. An empty day has a "Plan this day" button. It is the page the app opens with.
- **History** shows, per month, what was eaten (dinner and lunch), how often each meal and category came up, which meals have not been served for the longest time, and which have never been served. Days that are still ahead are listed as planned and not counted.

All soft rules (weekdays, weekly limits, variety) are relaxed rather than leaving a day empty.

## Shopping list
- Weeks run Monday–Sunday, or Sunday–Saturday if you choose that in Settings. Show 1, 2 or 4 weeks at once.
- Same ingredients merge (case and simple plurals ignored). Amounts are optional and added up per unit: `500 g beef` + `0,5 kg beef` = `1 kg beef`. Grams/kilograms and millilitres/centilitres/decilitres/litres are converted; fractions (`1/2`, `1 1/2`, `½`) and decimal commas work. Add your own units in Settings.
- Lines copied from recipe sites work too: long unit names (`2 tablespoons`, `200 grams`), ranges (`3-4 cloves` counts 4), `2 x 400 g`, `2 cups of flour`, text in brackets (`1 (14 oz) can beans`) and notes after a comma (`1 onion, finely chopped`) are understood, so they merge with plain `onion`.
- **Synonyms:** `red onion`, `yellow onion` and `white onion` count as `onion`; `scallion` is `spring onion`; `cilantro` is `coriander`; `ground beef` is `minced beef`; and so on. Add your own in Settings (`cherry tomato = tomato`); write `red onion = red onion` to keep one apart. Words like `large` or `small` before an ingredient are ignored.
- **Units on the list** (Settings): "as written in the recipes" keeps `dl` and `cl` when your recipes use them; "metric" and "imperial" convert everything (ounces, pounds and cups are then merged with grams and millilitres).
- **Aisles:** the list is grouped by aisle (Produce, Bakery, Meat & fish, Dairy & eggs, Pantry, …) using a built-in guess. Use an item's **⋯** button to move it to another aisle; that is remembered. The order of the groups follows the "shopping list order" in Settings.
- **Price and stock:** in the **⋯** dialog you can enter the price you paid (the item is ticked as bought, the last price is offered next time, and the list shows what you spent so far) or say you have enough at home for a week to three months. Such items leave the lists until then and wait under "In stock at home" with a "Need it again" button.
- **Household size** (Settings) scales amounts for meals that state their number of servings.
- **Things you always have** (Settings) never appear on the list (for salt and oil; use "in stock" for things that run out).
- **Share link** (Settings → Share the shopping list): a secret address that shows the list, lets people tick items and add extras, and nothing else (no plan, no meals, no settings). It works without the password, so it is meant for the people you shop with. "Make a new link" or "Stop sharing" disables the old address at once. Anyone who has the link can use it, so only send it to people you trust.
- Ticks and extra items ("washing-up liquid") are stored on the server, so every phone shows the same list. They are cleaned up automatically about three months after their week. Changing the first day of the week in Settings resets ingredient ticks; extra items are kept.
- **Offline use and "install as app" need HTTPS** (or `http://localhost`). Browsers refuse service workers on plain `http://server:8080`, so on a normal home-network setup the planner works as usual but without those two features. To get them, put it behind an HTTPS reverse proxy (or a tunnel that provides HTTPS). Settings shows whether they are available on the address you are using.

## Calendar feed
Settings shows an address ending in `/calendar.ics`. Subscribe to it in a calendar app to see the plan there.

## Adding 200 meals fast
Meals → Import. Paste blocks separated by blank lines:

    Lasagne | Pasta | yes
    - Minced beef
    - Onion

    Fish and chips
    Category: Fish
    Leftovers: No
    Ingredients:
    Cod
    Potatoes

Optional extra lines in a block: `Servings: 4`, `Prep: 30`, `URL: https://…`, `Notes: …`, `Weekdays: Sat, Sun`, `Leftover days: 2`, `Weight: favourite` (or `normal`, `rarely`), `Tags: quick, spicy`, `Months: Jun-Aug`, `Prep ahead: Marinate the chicken`, `Prep ahead days: 2`. In an `Ingredients:` line, separate items with commas or semicolons (decimal commas like `1,5 dl` are kept). Tick "Update meals that already exist" to change existing meals instead of skipping them.
Export gives the compact format back; the JSON backup also keeps the extra fields.

## Development
    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/ruff check .
    .venv/bin/pytest

The parsing of ingredient lines lives in `ingredients.py`; everything else is in `app.py` and `static/`. Upgrades from older databases are automatic.

The database is created and upgraded automatically (`PRAGMA user_version`). Mutating requests must send the header `X-Requested-With: fetch` (the web page does), which keeps other websites from changing your data through your browser.

## Notes
Without `AUTH_PASSWORD` there is no login: keep it on your home network or put it behind a reverse proxy with authentication.
