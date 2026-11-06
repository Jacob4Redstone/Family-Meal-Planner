# Family meal planner

Meals → automatic monthly planning → leftovers → weekly shopping lists. Nothing else.

## Run with Docker
    docker compose up -d --build
    # open http://<server>:8080

Data lives in the `mealplanner-data` volume (one SQLite file). Back up with `docker run --rm -v mealplanner-data:/d -v $PWD:/b busybox cp /d/mealplanner.db /b/`.

## Proxmox VE LXC (community-scripts menu)
`ct/mealplanner.sh` + `install/mealplanner-install.sh` use the same engine as the community-scripts (Default / Advanced settings menu).
(If the app sits in a subfolder of your repo, add it to the URL, e.g. `.../main/family-meal-planner`.)
1. Edit `REPO_URL` in `install/mealplanner-install.sh` to your GitHub repo and push.
2. On the Proxmox host shell:

       export COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/Jacob4Redstone/Family-Meal-Planner/main
       bash -c "$(curl -fsSL $COMMUNITY_SCRIPTS_URL/ct/mealplanner.sh)"

   `COMMUNITY_SCRIPTS_URL` makes the engine fetch `install/mealplanner-install.sh` from your repo instead of community-scripts'.
3. Update later: run `update` inside the container.

`proxmox/standalone-installer.sh` is a plain fallback with no menu and no dependency on the community-scripts engine.

## How planning works
- The pool is every meal you have. A meal is never used twice within 21 days (`GAP_DAYS`), counting days before and after the range, so adjacent months stay consistent.
- Leftovers = Yes reserves the next day as "Leftovers from …". That day adds nothing to the shopping list.
- Light variety rule: avoids the same category two days running when it can.
- If you have too few meals to honour the 21 days, it reuses the one served longest ago and tells you.
- "Pick another for me" re-rolls a single day. Edit any day by hand afterwards.
- Shopping lists run Monday–Sunday. Same ingredients merge (case and simple plurals ignored). Optional amounts like `500 g beef` or `2 onions` are added up per unit.

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

Export gives the same format back.

## Notes
No login: keep it on your home network or put it behind a reverse proxy with auth.
