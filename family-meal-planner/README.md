# Family meal planner

Meals → automatic monthly planning → leftovers → weekly shopping lists.

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
Use **Settings → Download backup** (a JSON file with meals, plan, settings and shopping ticks) and **Restore from file** to move or recover everything. For a raw database copy while the app runs:

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
- **Fixed days** (Settings): for example `Friday = Takeaway` puts that note on every Friday that planning fills.
- **Pinned days:** pin a day in its dialog and planning ("Replace" or "Pick another") never changes it.
- If you have too few meals to honour the gap, the one served longest ago is reused and you are told.
- **Undo:** "Undo last planning" restores the days as they were before the last plan or re-roll. "Replace" asks for confirmation first.
- Drag a day onto another to swap them (or use "Swap with another day" in the dialog). Leftover days move with their meal.

All soft rules (weekdays, weekly limits, variety) are relaxed rather than leaving a day empty.

## Shopping list
- Weeks run Monday–Sunday, or Sunday–Saturday if you choose that in Settings. Show 1, 2 or 4 weeks at once.
- Same ingredients merge (case and simple plurals ignored). Amounts are optional and added up per unit: `500 g beef` + `0,5 kg beef` = `1 kg beef`. Grams/kilograms and millilitres/centilitres/decilitres/litres are converted; fractions (`1/2`, `1 1/2`, `½`) and decimal commas work. Add your own units in Settings.
- **Household size** (Settings) scales amounts for meals that state their number of servings.
- **Things you always have** (Settings) never appear on the list.
- Ticks and extra items ("washing-up liquid") are stored on the server, so every phone shows the same list. Without a connection the last loaded list stays available (the page is installable as an app).

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

Optional extra lines in a block: `Servings: 4`, `Prep: 30`, `URL: https://…`, `Notes: …`, `Weekdays: Sat, Sun`, `Leftover days: 2`, `Weight: favourite` (or `normal`, `rarely`). In an `Ingredients:` line, separate items with commas or semicolons (decimal commas like `1,5 dl` are kept). Tick "Update meals that already exist" to change existing meals instead of skipping them.
Export gives the compact format back; the JSON backup also keeps the extra fields.

## Development
    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/ruff check .
    .venv/bin/pytest

The database is created and upgraded automatically (`PRAGMA user_version`). Mutating requests must send the header `X-Requested-With: fetch` (the web page does), which keeps other websites from changing your data through your browser.

## Notes
Without `AUTH_PASSWORD` there is no login: keep it on your home network or put it behind a reverse proxy with authentication.
