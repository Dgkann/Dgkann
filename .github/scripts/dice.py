"""Rolls the community dice when someone opens a "🎲 roll" issue.

Rules come from Dgkann/Dicegame: every roll adds to a shared pot, a 1 wipes it,
and whoever pushes it to 100 joins the hall of fame.
"""

import json
import os
import random
import re
from pathlib import Path

GOAL = 100
KEEP = 5

player = os.environ["PLAYER"]
owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "Dgkann")

state_path = Path("dice.json")
state = json.loads(state_path.read_text(encoding="utf-8"))

roll = random.SystemRandom().randint(1, 6)
state["total_rolls"] += 1

if roll == 1:
    state["score"] = 0
    result = "rolled a 1 and wiped the pot 💥"
else:
    state["score"] += roll
    result = f"rolled a {roll}"
    if state["score"] >= GOAL:
        state["score"] = 0
        state["winners"] = [player, *state["winners"]][:KEEP]
        result += f" and hit {GOAL}! 🏆"

state["recent"] = [{"player": player, "roll": roll}, *state["recent"]][:KEEP]
state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def mention(login):
    return f"[@{login}](https://github.com/{login})"


filled = state["score"] * 10 // GOAL
bar = "▰" * filled + "▱" * (10 - filled)
recent = " · ".join(f"{mention(r['player'])} `{r['roll']}`" for r in state["recent"])
winners = " · ".join(mention(w) for w in state["winners"]) or "nobody yet"

block = f"""<!-- DICE:START -->
<div align="center">

<img src="assets/dice-{roll}.png" width="72" alt="rolled {roll}"/>

**{mention(player)} {result}**

`{bar}` **{state['score']} / {GOAL}**

recent rolls: {recent}<br/>
🏆 hall of fame: {winners}<br/>
<sub>total rolls: {state['total_rolls']}</sub>

</div>
<!-- DICE:END -->"""

readme_path = Path("README.md")
readme, count = re.subn(
    r"<!-- DICE:START -->.*?<!-- DICE:END -->",
    lambda _: block,
    readme_path.read_text(encoding="utf-8"),
    flags=re.S,
)
if count != 1:
    raise SystemExit("DICE markers not found in README.md")
readme_path.write_text(readme, encoding="utf-8")

message = f"""🎲 @{player} {result}

`{bar}` **{state['score']} / {GOAL}**

See it live: https://github.com/{owner}"""

with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
    env.write(f"ROLL={roll}\n")
    env.write(f"MESSAGE<<DICE_MESSAGE_END\n{message}\nDICE_MESSAGE_END\n")
