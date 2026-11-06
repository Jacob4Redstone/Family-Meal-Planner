# Family meal planner

Meals, automatic planning, leftovers and weekly shopping lists.

Tabs: Today, Calendar, Shopping list, Meals, History and Settings.

## Run with Docker

    docker compose up -d --build

Open http://localhost:8080 (or http://SERVER:8080 from another device). Data is one SQLite file in a Docker volume named `<folder>_mealplanner-data`.

Optional settings go in a `.env` file next to `docker-compose.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_PASSWORD` | empty | Asks for this password (any user name). Use it behind HTTPS. |
| `BIND` | `0.0.0.0` | Address to publish on. Use `127.0.0.1` behind a reverse proxy on the same machine. |
| `PORT` | `8080` | Published port. |
| `GAP_DAYS` | `21` | A meal is not repeated within this many days. |
| `ENABLE_URL_IMPORT` | off | Set to `1` to fill in a meal from a recipe link. The server then downloads the page you give it. Private addresses are refused. |

## Proxmox

On the Proxmox host shell:

    export COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/Jacob4Redstone/Family-Meal-Planner/main/family-meal-planner
    bash -c "$(curl -fsSL $COMMUNITY_SCRIPTS_URL/ct/mealplanner.sh)"

Update later by running `update` inside the container. To set `AUTH_PASSWORD`, run `systemctl edit mealplanner`, add `[Service]` and `Environment=AUTH_PASSWORD=...`, then `systemctl restart mealplanner`. `proxmox/standalone-installer.sh` is a plain alternative that does not need the community-scripts engine.

## Planning

- A meal is not used twice within 21 days, counting days before and after the range.
- A meal can make leftovers for 1 to 3 days. Leftover days add nothing to the shopping list. A reserved day is never overwritten: if the next day is already planned you get a message and that leftover day is skipped. Clearing a leftover day also frees the ones after it.
- Planning avoids the same category two days in a row, prefers favourites, and respects weekday limits on a meal, weekly category limits (`Pasta = 2`), fixed days (`Friday = Takeaway`) and tag rules (`Mon-Fri = quick`).
- Meals can be limited to months (seasons).
- The "never plan" list in Settings excludes meals by word, for example `peanut`. Excluded meals are marked in the Meals list.
- Pin a day to keep it when planning again. Drag a day onto another to swap them, or use "Swap with another day" in its dialog. Leftover days move with their meal.
- Changing a meal's leftover setting only affects days from today on.
- "Suggest meals" in a day's dialog shows three meals that fit that day.
- "Undo last planning" restores the days as they were before the last plan or re-roll. It is offered for one hour, and only until you change the plan another way (editing a day, swapping, deleting a meal), so it can never wipe later edits. "Replace" asks for confirmation first.
- If there are too few meals, the one served longest ago is reused and you are told.
- Soft rules are relaxed rather than leaving a day empty.

## Lunch, cooks and reminders

- Each day has an optional lunch (a meal, leftovers or a note), filled in by hand. Automatic planning, the repeat gap, swapping and undo never change it. Lunch meals count for the shopping list, the calendar feed, "last served" and History.
- Settings can name who cooks on which weekdays (`Mon-Fri = Anna`). You can pick another cook for a single day.
- A meal can have a "prepare ahead" reminder, shown 1 to 3 days before.

## Today and History

- Today shows today and tomorrow: dinner, lunch, cook, notes, recipe link, ingredients and due reminders.
- History shows what was eaten each month, counts per meal and category, meals not served for the longest time, and meals never served.

## Shopping list

- Weeks run Monday to Sunday, or Sunday to Saturday (Settings). You can show 1, 2 or 4 weeks.
- Duplicate ingredients merge, ignoring case and simple plurals. Amounts are optional and added up per unit: `500 g beef` and `0,5 kg beef` become `1 kg beef`. Grams and kilograms, and millilitres to litres, are converted.
- Fractions (`1/2`, `1 1/2`), decimal commas, ranges (`3-4 cloves` counts 4), `2 x 400 g`, long unit names (`2 tablespoons`), text in brackets (`1 (14 oz) can beans`) and notes after a comma (`1 onion, finely chopped`) are understood. You can add your own units in Settings.
- Synonyms merge items (`red onion` and `onion`). Add your own in Settings.
- Units can stay as written in the recipes, or be shown as metric or imperial.
- Items are grouped by aisle. Use the three-dot button on an item to change its aisle, enter a price paid, or mark it as in stock at home for a while.
- A household size scales amounts for meals that state their servings. Items you always have can be hidden.
- Ticks and extra items are stored on the server, so every device sees the same list. They are cleaned up about three months after their week. Changing the first day of the week resets ingredient ticks; extra items are kept.
- Share link (Settings): a secret address where people can see the list, tick items and add extras, without the password. They cannot see anything else. Making a new link or stopping sharing disables the old one. Only send it to people you trust.
- Offline use and "install as app" need HTTPS or `http://localhost`. Over plain `http://server:8080` the app works normally without them.

## Importing meals

Meals tab, Import. Paste blocks separated by blank lines:

    Lasagne | Pasta | yes
    - Minced beef
    - Onion

    Fish and chips
    Category: Fish
    Leftovers: No
    Ingredients:
    Cod
    Potatoes

Separate items in an `Ingredients:` line with commas or semicolons (decimal commas like `1,5 dl` are kept). Optional extra lines: `Servings`, `Prep`, `URL`, `Notes`, `Weekdays`, `Leftover days`, `Weight` (favourite, normal, rarely), `Tags`, `Months`, `Prep ahead`, `Prep ahead days`. Tick "Update meals that already exist" to change existing meals instead of skipping them. Export gives the same format back.

## Backup

Settings has "Download backup" and "Restore from file" (meals, plan, lunches, settings, shopping data). The share link is not included. A calendar feed for your phone is at `/calendar.ics`.

## Development

    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/ruff check .
    .venv/bin/pytest

Ingredient parsing is in `ingredients.py`, the rest of the server in `app.py`, the pages in `static/`. The database is created and upgraded automatically. Changing requests must send the header `X-Requested-With: fetch` (the pages do), which stops other websites from changing your data through your browser.

Without `AUTH_PASSWORD` there is no login. Keep the app on your home network or behind a reverse proxy with authentication.
