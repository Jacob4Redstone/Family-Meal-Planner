# Family Meal Planner

A small self-hosted app for planning family dinners and building the weekly shopping list. AI generated code.

<img width="1920" height="760" alt="example-meals-one-month" src="https://github.com/user-attachments/assets/502a2e15-33a5-40f5-8b05-760991ee5186" />

## How it behaves

Planning
- The pool is every meal you have. It checks 21 days before and after the range, so a month planned today still fits with the month before or after it.
- You can plan the month shown or one week. It either fills only the empty days or replaces everything in the range. Days you have pinned are kept, and the last planning can be undone for an hour.
- If you have too few meals to avoid repeats, it reuses the meal served longest ago and tells you.
- A light variety rule avoids the same category two days running when it can.
- Optional rules: favourites and "rarely" meals, weekday limits on a meal, months a meal suits, tags with rules such as "Mon-Fri = quick", weekly category limits such as "Pasta = 2", and fixed days such as "Friday = Takeaway". These are soft rules: they are relaxed rather than leaving a day empty.
- A "never plan meals containing" list in Settings excludes meals by word, for allergies and dislikes.

Leftovers
- A meal that makes leftovers reserves the next 1 to 3 days as "Leftovers from ...". Those days add nothing to the shopping list, and a day that is already planned is never overwritten.

Editing a day
- Tap a day to change it. You can pick a meal, mark it as leftovers, add a note like "Eating out", pin or clear the day, swap it with another day (or drag it there), ask for another meal at random, or ask for suggestions that fit that day.
- Each day also has an optional lunch, filled in by hand, and an optional cook. Settings can set a cook per weekday.
- A meal can carry a "prepare ahead" reminder that appears 1 to 3 days before it.

Pages
- Today shows today and tomorrow in large text, with notes, recipe link, ingredients and any due reminder.
- Calendar is a monthly grid that prints on A4 landscape, with striped leftover days. On a phone it becomes a list of weeks.
- History shows what was eaten each month, how often each meal and category came up, and which meals have not been served for a long time.

Shopping list
- Each week runs Monday to Sunday (or Sunday to Saturday) and merges duplicate ingredients, ignoring case, simple plurals and synonyms such as "red onion" and "onion".
- Amounts are optional: "500 g beef", "1/2 onion" and "2 onions" are added up per unit, and g/kg and ml/dl/l are converted. Lines copied from recipe sites work too.
- Items are grouped by aisle. Per item you can change the aisle, enter a price, or mark it as in stock at home for a while.
- It can scale to your household size, hide things you always have, show several weeks, and take extra items. Ticks are shared between devices.
- A secret share link lets the people you shop with see the list, tick items and add extras, without the password. They can see nothing else.

Import, export and backup
- You can paste meals in bulk, in a "Category: / Leftovers: / Ingredients:" format or a compact "Name | Category | yes" format. This makes entering 200 meals practical, and export gives the compact format back.
- Settings has a full JSON backup and restore, and a calendar feed (`/calendar.ics`) you can subscribe to from a phone.

## Deploying

Docker: run `docker compose up -d --build` in the `family-meal-planner` folder and open port 8080. The data is one SQLite file in a named volume.

Proxmox: in the host shell run

    export COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/Jacob4Redstone/Family-Meal-Planner/main/family-meal-planner
    bash -c "$(curl -fsSL $COMMUNITY_SCRIPTS_URL/ct/mealplanner.sh)"

Without a password there is no login, so keep it on your home network or put it behind a reverse proxy with authentication. You can also set `AUTH_PASSWORD` to protect the whole app (the share link keeps working without it, but only for the shopping list).

Settings such as the password, port and repeat gap, and everything else in more detail, are in [family-meal-planner/README.md](family-meal-planner/README.md).
