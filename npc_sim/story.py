import re
from typing import Any

from npc_sim.models import StrictModel


class Paragraph(StrictModel):
    id: str
    text: str


def first_person(text: str) -> str:
    for source, replacement in (
        ("You are", "I am"), ("You", "I"), ("you are", "I am"), ("you", "I"),
        ("Your", "My"), ("your", "my"),
    ):
        text = re.sub(r"\b" + source + r"\b", replacement, text)
    return text


def named_voice(text: str, name: str) -> str:
    """Render the narrator's present-tense templates with a named subject."""
    verbs = {
        "am": "is", "have": "has", "do": "does", "go": "goes",
        **{verb: verb[:-1] + "ies" for verb in ("try", "carry", "fly")},
        **{verb: verb + "s" for verb in (
            "take", "look", "open", "drop", "unlock", "hit", "read", "slice",
            "dice", "spread", "assemble", "serve", "wait", "measure", "dig",
            "learn", "discover", "notice", "need", "observe", "pick", "put",
            "use", "give", "say", "swim", "move", "recover", "speak", "arrive",
            "remain", "mark", "place", "plug", "unplug", "turn", "add",
            "grind", "cook", "recruit", "sail", "want", "know", "see",
            "walk", "cross", "anchor", "repair", "prepare", "secure", "leave",
        )},
    }
    text = re.sub(r"\bI['’](m|ve|ll|d)\b",
                  lambda match: "I " + {"m": "am", "ve": "have", "ll": "will", "d": "would"}[match[1]], text)
    text = re.sub(r"\bI (don't|can't|won't)\b",
                  lambda match: "I " + {"don't": "do not", "can't": "cannot", "won't": "will not"}[match[1]], text)
    text = re.sub(r"\bI ([a-z]+)\b", lambda match: name + " " + verbs.get(match[1], match[1]), text)
    text = re.sub(r"\bI\b|\bme\b", lambda _: name, text)
    text = re.sub(r"\b[Mm]y\b|\bmine\b", lambda _: name + "'s", text)
    text = re.sub(r"\bmyself\b", lambda _: name, text)
    return re.sub(r"(^|[.!?]\s+)" + re.escape(name) + r"\b",
                  lambda match: match[1] + name, text, flags=re.IGNORECASE)


def team_narration(events: list[dict[str, Any]]) -> bool:
    return any(event["kind"] == "observation" and "role" in event["payload"] for event in events)


def readable(text: str, names: dict[str, str]) -> str:
    text = re.sub(
        r"\b(You use|I use|\w+ uses) ([\w-]+): (?:Use [^.!?]+? to )?([A-Z])",
        lambda match: f"{match[1]} {match[2]} to {match[3].lower()}", text)

    def label(match: re.Match[str]) -> str:
        token = match[0]
        if token in names:
            return names[token].lower()
        return "underwater engineering" if token == "water_engineering" else token.replace("_", " ")

    return re.sub(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b", label, text.replace("`", ""))


def intent(action: str, args: dict[str, Any], names: dict[str, str],
           challenges: dict[str, str] | None = None) -> str:
    def name(key: str) -> str:
        value = args.get(key)
        return f"the {names.get(value, value).lower()}" if isinstance(value, str) else ""

    target, item = name("target"), name("item")
    description = (challenges or {}).get(action)
    if description:
        return f"I try to {description[0].lower()}{description[1:].rstrip('.')}."
    sentences = {
        "look": f"I take a closer look at {target}." if target else "I look around.",
        "search": f"I try searching {target} for concealed features.",
        "open": f"I try opening {target}.", "take": f"I try to pick up {target}.",
        "drop": f"I try to put down {target}.", "unlock": f"I try unlocking {target} with {item}.",
        "hit": f"I try hitting {target} with {item}.", "go": f"I try going through {target}.",
        "read": f"I try reading {target}.", "slice": f"I try slicing {target} with {item}.",
        "dice": f"I try dicing {target} with {item}.",
        "spread": f"I try spreading {name('ingredient')} on {target} with {item}.",
        "assemble": f"I try putting {name('first')} and {name('second')} together.",
        "serve": f"I try serving {target} on {name('surface')}.",
        "teleport": "I try speaking the journal's travel word.",
        "cast_unlock": f"I try casting Unlock on {target}.",
        "cast_shrink": "I try casting Shrink on myself and my equipment.",
        "cast_restore_size": "I try restoring my original size.",
        "recruit": f"I try recruiting {target} with {item}.",
        "sail": "I try sailing to the other island.",
        "wait_tide": "I wait safely for the next tide.",
        "pace": f"I measure {args.get('count')} paces at {target}.",
        "dig": f"I try digging at {target} with {item}.",
        "give": f"I try handing {target} to {str(args.get('recipient', 'a teammate')).replace('-', ' ').replace('_', ' ').title()}.",
        "say": f'I say, "{args.get("message", "")}"',
        "wait": "I wait for the work in progress.",
        "play_rps": f"I try committing {args.get('move')} privately for this round.",
        "play_tic_tac_toe": f"I try placing my mark in cell {args.get('cell')}.",
        "erp_terminal": f'I enter "{args.get("command", "")}" in the ERP terminal.',
        "adjust_bbq_heat": f'I adjust the BBQ heat dial to {args.get("heat_percent")}% and watch the temperature gauge.',
        "monitor_bbq": "I watch the BBQ temperature gauge without changing the heat.",
    }
    settings = [f"{key.replace('_', ' ')} set to {value}" for key, value in args.items()
                if key != "target" and isinstance(value, (str, int, float, bool))]
    return sentences.get(action, f"I try to {action.replace('_', ' ').replace('-', ' ')}"
                         + (f" at {target}" if target else "")
                         + (" with " + ", ".join(settings) if settings else "") + ".")


def build_narrative(events: list[dict[str, Any]], actor_id: str | None = None) -> list[Paragraph]:
    if actor_id is None:
        ordered = sorted(events, key=lambda event: event["sequence"])
        actors = dict.fromkeys(event["actor_id"] for event in ordered)
        if len(actors) == 1:
            return build_narrative(events, next(iter(actors)))
        source = {event["id"]: event for event in ordered}
        combined = [paragraph for actor in actors for paragraph in build_narrative(events, actor)]
        combined.sort(key=lambda paragraph: source[paragraph.id]["sequence"])
        openings: set[str] = set()
        result = []
        for paragraph in combined:
            event = source[paragraph.id]
            if event["kind"] == "observation" and event["payload"].get("opening"):
                opening = event["payload"]["narrative"]
                if opening in openings:
                    continue
                openings.add(opening)
            result.append(paragraph)
        return result
    team = team_narration(events)
    actor_name = actor_id.replace("-", " ").replace("_", " ").title()

    def voice(text: str) -> str:
        return named_voice(text, actor_name) if team else text

    paragraphs: list[Paragraph] = []
    names: dict[str, str] = {}
    challenges: dict[str, str] = {}
    facts: dict[str, dict[str, Any]] = {}
    decisions: dict[int, dict[str, Any]] = {}
    seen: set[str] = set()
    location: str | None = None
    observed = False
    pending_cycle: int | None = None
    pending_reason = ""

    def learn(value: dict[str, Any]) -> tuple[list[str], list[str]]:
        discoveries, details = [], []
        challenges.update({challenge["id"]: challenge["description"] for challenge in value.get("skill_challenges", [])})
        for entity in value["visible_entities"]:
            eid = entity["id"]
            name = entity.get("name", eid)
            previous = facts.get(eid, {})
            if eid not in names:
                discoveries.append(f"the {name.lower()}")
            names[eid] = name
            if previous.get("description") != entity["description"]:
                details.append(first_person(entity["description"]))
            if entity.get("power") is not None and (
                    previous.get("power") != entity["power"] or previous.get("plugged_into") != entity.get("plugged_into")):
                connection = ("unplugged" if entity.get("plugged_into") is None else
                              f"connected to the {names.get(entity['plugged_into'], entity['plugged_into']).lower()}")
                details.append(f"The {name.lower()} is switched {entity['power']} and {connection}.")
            if entity.get("ingredients") and previous.get("ingredients") != entity["ingredients"]:
                details.append(f"The {name.lower()} contains {', '.join(entity['ingredients'])}.")
            if entity.get("food") and (not previous.get("food") or previous.get("nutrition") != entity.get("nutrition")):
                nutrition = entity.get("nutrition")
                if nutrition is None:
                    details.append(f"The {name.lower()}'s nutrition is unknown.")
                else:
                    number = lambda key: f"{nutrition[key]:g}"
                    details.append(
                        f"The {name.lower()}'s nutrition is {number('serving_grams')} g per portion: "
                        f"{number('calories_kcal')} kcal; protein {number('protein_g')} g, "
                        f"carbs {number('carbohydrates_g')} g, fat {number('fat_g')} g, "
                        f"fiber {number('fiber_g')} g and sugar {number('sugar_g')} g.")
            preparation = entity.get("preparation", {})
            if previous.get("preparation") != preparation:
                if "cut" in preparation:
                    details.append(f"The {name.lower()} is {preparation['cut']}.")
                if "topping" in preparation:
                    topping = preparation["topping"]
                    details.append(f"The {name.lower()} has no topping." if topping == "plain" else
                                   f"The {name.lower()} has a spread." if topping == "filled" else
                                   f"The {name.lower()} is coated with {topping.replace('-', ' ')}.")
                if "cooking" in preparation:
                    details.append(f"The {name.lower()} is {preparation['cooking']}.")
            facts[eid] = entity
        return discoveries, details

    for event in sorted(events, key=lambda event: event["sequence"]):
        if event["id"] in seen or event["actor_id"] != actor_id:
            continue
        seen.add(event["id"])
        payload, kind, cycle = event["payload"], event["kind"], event["cycle"]
        text, prepend = "", False
        if kind == "started":
            objective = payload["goal"]["objective"]
            text = ("I do not yet know my assigned task. I need to explore and find instructions."
                    if payload["goal"].get("revealed") is False
                    else f"{'Our' if team else 'My'} goal is to {objective[0].lower()}{objective[1:]}")
        elif kind == "goal_discovered":
            source = names.get(payload["source_id"], payload["source_id"]).lower()
            text = f"After reading the {source}, I learn my task: {payload['goal']['objective']}"
        elif kind == "decision":
            decisions[cycle] = payload
            continue
        elif kind == "observation":
            discoveries, details = learn(payload)
            moved = location != payload["self"]["location_id"]
            if not observed:
                carrying = payload["self"]["body"]["carrying"]
                if carrying["rule"] in {"item_count", "inventory_units"}:
                    noun = "inventory unit" if carrying["rule"] == "inventory_units" else "item"
                    details.insert(0, f"I can carry at most {carrying['limit']} {noun}{'' if carrying['limit'] == 1 else 's'} at a time.")
                text = first_person(payload["narrative"])
                prepend = payload.get("opening", False)
                if prepend:
                    discoveries, details = [], []
                observed = True
            elif moved:
                text = first_person(payload["narrative"])
            if discoveries:
                joined = ", ".join(discoveries[:-1]) + " and " + discoveries[-1] if len(discoveries) > 1 else discoveries[0]
                text += f" {'I can see' if prepend else 'I discover'} {joined}."
            if details:
                text += " " + " ".join(details)
            location = payload["self"]["location_id"]
        elif kind == "action_request":
            decision = decisions.get(cycle, {})
            reason = decision.get("justification", "") if decision.get("action_id") == payload["action_id"] else ""
            text = (first_person(reason) + ("" if reason.endswith((".", "!", "?")) else ".") + " ") if reason else ""
            pending_reason = text
            text += intent(payload["action_id"], payload["arguments"], names, challenges)
            pending_cycle = cycle
        elif kind == "action_result":
            value = payload["observation"]
            discoveries, details = learn(value)
            text = first_person(payload["explanation"])
            if payload.get("outcome") == "APPLIED":
                text = text.replace("I pick up ", "I picked up ", 1)
            if location != value["self"]["location_id"]:
                text += " " + first_person(value["narrative"])
            if discoveries:
                joined = ", ".join(discoveries[:-1]) + " and " + discoveries[-1] if len(discoveries) > 1 else discoveries[0]
                text += f" I discover {joined}."
            text += (" " + " ".join(details)) if details else ""
            location = value["self"]["location_id"]
            if pending_cycle == cycle and paragraphs:
                if payload.get("outcome") == "APPLIED":
                    paragraphs[-1].text = readable(pending_reason + text, names)
                else:
                    paragraphs[-1].text += " " + readable(text, names)
                pending_cycle = None
                continue
        elif kind == "error":
            text = f"My attempt was interrupted. {payload['message']}"
        elif kind == "finished":
            text = (("The world confirms that we have achieved our goal." if team else
                     "The world confirms that I have achieved my goal.") if payload["status"] == "succeeded"
                    else f"My attempt has ended. {payload['reason']}")
        else:
            continue
        if text.strip():
            paragraph = Paragraph(id=event["id"], text=readable(text.strip(), names))
            if prepend:
                paragraphs.insert(0, paragraph)
            else:
                paragraphs.append(paragraph)
            if kind != "action_request":
                pending_cycle = None
    if team:
        for paragraph in paragraphs:
            paragraph.text = voice(paragraph.text)
    return paragraphs
