# Family-Meal-Planner
AI generated code
-------------
How it behaves

Planning: the pool is every meal you have. It checks 21 days before and after the range, so a month planned today still fits with the month before or after it.
Leftovers: a meal with Leftovers = Yes reserves the next day as “Leftovers from <Meal>“. That day adds nothing to the shopping list.
Small pool: if you have too few meals to avoid repeats, it reuses the meal served longest ago and tells you.
Variety: I added a light rule that avoids the same category two days running when it can.
Plan meals: you can plan the month shown or one week. It either fills only the empty days or replaces everything in the range.
Editing: tap a day to change it. You can pick a meal, mark it as leftovers, add a note like “Eating out”, clear the day, or ask for another meal at random. Changing a meal that had leftovers frees the next day again.
Shopping list: each week runs Monday to Sunday and merges duplicate ingredients, ignoring case and simple plurals. Amounts are optional: “500 g beef” and “2 onions” are added up per unit. Each week row in the calendar links to that week’s list, and you can tick items off, copy the list, or print it.
Calendar: the monthly calendar is a printable A4 landscape grid, with striped leftover days. On a phone it turns into a list of weeks.
Import and export: you can paste meals in bulk, in your own “Category: / Leftovers: / Ingredients:” format or a compact Name | Category | yes format. This makes entering 200 meals practical, and export gives the same format back.

Deploying

Docker: run docker compose up -d --build and open port 8080. The data is one SQLite file in a named volume.
Proxmox: Open Shell run:
export COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/Jacob4Redstone/Family-Meal-Planner/main/family-meal-planner
bash -c "$(curl -fsSL $COMMUNITY_SCRIPTS_URL/ct/mealplanner.sh)"

There is no login, so keep it on your home network or put it behind a reverse proxy with authentication.
