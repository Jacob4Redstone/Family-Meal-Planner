# Family-Meal-Planner
AI generated code
<img width="1920" height="760" alt="example-meals-one-month" src="https://github.com/user-attachments/assets/502a2e15-33a5-40f5-8b05-760991ee5186" />

-------------
How it behaves

Planning: the pool is every meal you have. It checks 21 days before and after the range, so a month planned today still fits with the month before or after it.
Leftovers: a meal with Leftovers = Yes reserves the next 1–3 days (your choice per meal) as “Leftovers from <Meal>“. Those days add nothing to the shopping list, and a day that is already planned is never overwritten.
Small pool: if you have too few meals to avoid repeats, it reuses the meal served longest ago and tells you.
Variety and rules: it avoids the same category two days running when it can, and also honours optional weekday rules per meal, favourites/“rarely” weights, weekly category limits, fixed days such as “Friday = Takeaway”, and prefers meals that share ingredients within a week.
Plan meals: you can plan the month shown or one week. It either fills only the empty days or replaces everything in the range (pinned days stay). The last planning can be undone.
Editing: tap a day to change it. You can pick a meal, mark it as leftovers, add a note like “Eating out”, pin or clear the day, swap it with another day (or drag it there), or ask for another meal at random. Changing a meal that had leftovers frees those days again.
Shopping list: each week runs Monday to Sunday (or Sunday to Saturday) and merges duplicate ingredients, ignoring case and simple plurals. Amounts are optional: “500 g beef”, “1/2 onion” and “2 onions” are added up per unit (g/kg and ml/dl/l are converted). It can scale to your household size, hide things you always have at home, show several weeks, and take extra items. Ticks are shared between devices. Each week row in the calendar links to that week’s list, and you can copy the list or print it.
Tags, seasons, rules and suggestions: meals can carry tags and months; Settings has tag rules (“Mon-Fri = quick”), weekly limits, fixed days, a “never plan meals containing…” list for allergies and dislikes, and the day dialog can suggest meals that fit.
Lunch, cooks and reminders: a hand-filled lunch slot, a cook per weekday (changeable per day) and “prepare ahead” reminders the day before.
Today and History: a Today page with today’s and tomorrow’s dinner, notes, recipe link and reminders, and a History page with what was eaten, counts and forgotten meals.
Shopping extras: aisle grouping (editable), price paid, “in stock at home”, synonyms (red onion = onion), metric/imperial display, and a secret share link for the people you shop with.
Calendar: the monthly calendar is a printable A4 landscape grid, with striped leftover days. On a phone it turns into a list of weeks. A calendar feed (`/calendar.ics`) can be subscribed to from a phone.
Import and export: you can paste meals in bulk, in your own “Category: / Leftovers: / Ingredients:” format or a compact Name | Category | yes format. This makes entering 200 meals practical, and export gives the same format back. Settings has a full JSON backup and restore.

Deploying

Docker: run docker compose up -d --build and open port 8080. The data is one SQLite file in a named volume. Optional settings (password, bind address, gap days) are described in `family-meal-planner/README.md`.
Proxmox: Open Shell run:
export COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/Jacob4Redstone/Family-Meal-Planner/main/family-meal-planner
bash -c "$(curl -fsSL $COMMUNITY_SCRIPTS_URL/ct/mealplanner.sh)"

Without a password there is no login, so keep it on your home network or put it behind a reverse proxy with authentication. You can also set `AUTH_PASSWORD` to protect the whole app.
