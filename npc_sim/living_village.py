import argparse
from pathlib import Path

from npc_sim import village_economy, village_events, village_social, village_maintenance, village_mining, village_livestock, village_communication, village_firewood, village_equipment
from npc_sim import village_depot
from npc_sim.models import Entity, Scenario, VillageEconomyConfig, VillageEventsConfig, VillageSocialConfig, VillageMaintenanceConfig, VillageResearchConfig
from npc_sim.village_equipment_models import VillageEquipmentConfig
from npc_sim.storage import read_json, write_json


def build_scenario(base: Scenario, *, livestock: bool = True, anti_idle: bool = False, firewood: bool = False,
                   purposeful_downtime: bool = False) -> Scenario:
    scenario = base.model_copy(deep=True)
    if scenario.village is None or not scenario.village.expanded_rules:
        raise ValueError("The living village requires an expanded timed village")
    scenario.id = "millhaven-living"
    scenario.title = "Millhaven III - Work, Friends and Visitors"
    config = scenario.village
    for rid, resident in config.residents.items():
        wake = 330 if rid == "elsie" else 420 if resident.wake_minute == 480 else resident.wake_minute
        if wake != resident.wake_minute:
            for entry in resident.schedule:
                if entry.minute == resident.wake_minute:
                    entry.minute = wake
            resident.wake_minute = wake
    for agent in scenario.agents:
        if agent.id == "elsie":
            agent.role = agent.role.replace("wake at 06:00", "wake at 05:30")
    config.breakfast = True
    config.event_driven_waits = True
    config.anti_idle = anti_idle or purposeful_downtime
    config.purposeful_downtime = purposeful_downtime
    config.require_tea_use = False
    config.require_manure_use = False
    config.demand_led_completion = True
    config.meal_price = 2
    economy = village_economy.example_config(scenario)
    economy.update(subsidy_per_meal=0, subsidy_payer=None)
    config.economy = VillageEconomyConfig.model_validate(economy)
    social = village_social.example_config()
    scenario.entities.update({identifier: Entity.model_validate(fixture)
                              for identifier, fixture in village_social.example_fixtures().items()})
    config.social = VillageSocialConfig.model_validate(social)
    config.events = VillageEventsConfig.model_validate(village_events.recommended_config(scenario, initial_example=True))
    config.maintenance = VillageMaintenanceConfig.model_validate(village_maintenance.example_config(scenario))
    config.research = VillageResearchConfig()
    village_mining.install_scenario(scenario)
    config.equipment = VillageEquipmentConfig.model_validate(village_equipment.recommended_config(scenario))
    if livestock:
        village_livestock.install_scenario(scenario)
    village_communication.install_scenario(scenario)
    if firewood:
        village_firewood.install_scenario(scenario)
        config.firewood.site_capacities[config.residents[config.firewood.household_buyer].work] = 24
        opening = {
            "tavern": {"firewood": 12}, "bakery": {"firewood": 10, "flour": 8},
            "forge": {"firewood": 10}, "cottages": {"firewood": 24},
            "workshop": {"timber": 6}, "watermill": {"grain": 8},
            "farm": {"hay": 8}, "stable": {"hay": 6},
        }
        for site, goods in opening.items():
            config.stocks[site].update(goods)
        config.initial_stock_seed_id = "millhaven-living:initial-village-stock:v1"
    if purposeful_downtime:
        village_social.install_optional_goods(scenario)
        merchant = next(item for item in config.events.templates if item.id == "visiting-merchant")
        scheduled = config.start_minute // 1440 * 1440 + merchant.window_start
        config.events = VillageEventsConfig.model_validate(
            village_events.offer_optional_goods(config.events.model_dump(), scheduled_at=scheduled))
    config.spoil_after.update({
        "meals": 360, "fish": 720, "milk": 1440, "bread": 2880,
        "eggs": 10080, "herbs": 4320,
        "tea": 129600, "flour": 43200, "grain": 129600, "hay": 86400,
        "plant-scraps": 1440, "compost-material": 4320,
        "compost": 129600, "manure": 43200,
    })
    if "preserved-fish" in config.prices:
        config.spoil_after["preserved-fish"] = 7200
    scenario.objective = (
        "Restore the well, mill and working wagon using the real crew, tools and supplies. "
        "Meet accepted orders and maintain configured reserves without unnecessary production. "
        "Eat breakfast after waking and supper at 19:00 at the tavern, paying the configured price. "
        "Use interests, personal projects, music and optional quests during genuinely free time. "
        "Respond to locally discovered events and trade with visitors only when affordable and useful. "
        "Complete assigned daily town maintenance at its real sites; unfinished jobs carry forward at midnight. "
        "Use free workday time for occupational research; verified discoveries unlock real recipes and useful goods. "
        f"Keep iron renewable: mine ore, haul it and smelt it with real {'firewood' if firewood else 'logs'}, or buy the daily trader's finite consignment in person. "
        "Travel home by bedtime, sleep adequately, stop routes and put the real horse away. "
        "Leisure, sidequests, and visitor departures are not mandatory resident victory conditions."
    )
    if firewood:
        scenario.objective += " Keep occupied cottage hearths and working ovens/forge supplied with paid, physically delivered firewood."
    if livestock:
        scenario.objective += (
            " Care daily for the physical farm cow and twelve hens using real balanced feed, well water and clean housing. "
            "Collect milk/eggs only for actual funded demand. Recycle only fresh plant surplus as chicken supplements; "
            "spoiled food becomes real compost material, never animal feed."
        )
    scenario.opening_narrative = (
        "Millhaven is waking to a day of work and ordinary village life. Breakfast follows each "
        "villager's wake time; supper is at 19:00. Suppliers produce against real demand and small "
        "reserves. Buyers may post one funded supply request at the physical square board or "
        "write at the supplier's physical workplace inbox. Only actual readers or listeners learn it; "
        "replies and project appointments need real channels. Continue useful work while waiting. Meals cost two coins. "
        "Elsie coordinates the inn, Iona is writing her herbal book, and interested villagers can "
        "rehearse or play music together. Hobbies and sidequests are optional. Passing visitors "
        "and minor events may change plans, but everyone still needs time to eat, care for the "
        "horse, travel home and sleep."
    )
    for agent in scenario.agents:
        agent.role += (
            " Follow configured meal prices and breakfast/supper schedules rather than legacy "
            "single-meal instructions. Check demand and accepted orders before producing or "
            "shipping. Use the shared physical communication policy: one board or workplace-inbox "
            "request per need, personally learned replies, relevant local sharing, and useful work "
            "while waiting. In-person ordering remains possible, but do not chase absent suppliers "
            "repeatedly. No remote messages or knowledge of anyone's current whereabouts. "
            "This replaces legacy global bell broadcasts and live noticeboard stock reports. "
            "Use event-based waiting when appropriate. Personal interests and quests "
            "are optional and never override meals, funded commitments, animal care or bedtime."
            " Do your assigned daily maintenance before optional leisure, using actual sites, time and supplies. "
            "Routine maintenance does not replace structural repairs; unfinished jobs carry forward."
            " During genuinely free workday time, propose and research new items related to your occupation. "
            "Use actual ingredients, equipment and fallible trials; discoveries persist and unlock verified production/use. "
            "Research never overrides food, sleep, maintenance or accepted supplier commitments."
            + (" " + village_firewood.POLICY + " Rowan supplies firewood; Iona funds the communal cottage pile. "
               "Prioritize funded fuel deliveries alongside existing timber commitments; do not keep taking the automatic timber-only "
               "route when firewood orders need manual foot delivery. Inspect your actual fuel stock before posting a request."
               if firewood else "")
            + (" Livestock is physical: Ada cares daily for one cow and twelve hens at farm using real hay/grain, "
            "a carried water bucket filled at the completed well, housing and tools. Protect horse hay and funded grain. "
            "Milk/eggs are timed, daytime, daily-limited and only for actual reserves/accepted demand. "
            "Elsie can order these foods from Ada through a valid physical channel, then cook one real milk plus one egg into two meals "
            "using village_cook_dairy; preserve existing bread/fish orders. No butter/cheese work." if livestock else "")
            + (" Safe recycling is deliberate: convert only fresh proven plant-only surplus into plant-scraps; "
               "one scrap may supplement twelve hens' three-unit ration, leaving at least two grain. "
               "Never feed spoiled/mouldy food or animal ingredients. Actual spoilage produces compost-material "
               "at its physical site; trade/haul it to farm, then use the actual pile/fork and timed village_compost "
               "to process two units into one compost fertilizer. village_fertilize_compost uses this real fertilizer "
               "without inventing manure." if livestock else "")
        )
    separate_herbal_workshop(scenario)
    return Scenario.model_validate(scenario.model_dump(mode="json"))


def separate_herbal_workshop(scenario: Scenario) -> None:
    config = scenario.village
    healer = config.residents["iona"]
    if healer.home != "cottages" or healer.work != "cottages" or "herbal-workshop" in scenario.entities:
        raise ValueError("Expected Iona's workshop to be part of the cottages")
    scenario.entities["cottages"].description = "Shared courtyards and modest homes."
    scenario.entities["herbal-workshop"] = Entity(
        name="Herbal Workshop", kind="location",
        description="Iona lives and prepares herbal tea here, with a small garden outside.")
    scenario.entities["cottages-herbal-workshop"] = Entity(
        name="Herbal workshop path", kind="door", location="cottages", destination="herbal-workshop",
        description="A short walk from the cottages to Iona's workshop.", open=True)
    healer.home = healer.work = healer.job.location = "herbal-workshop"
    for entry in healer.schedule:
        if entry.location == "cottages":
            entry.location = "herbal-workshop"
    next(agent for agent in scenario.agents if agent.id == "iona").start_location = "herbal-workshop"
    for item in ("herb-pouch", "mortar", "workplace-inbox-iona", "plant-notes", "herb-garden-plot"):
        if item in scenario.entities:
            scenario.entities[item].location = "herbal-workshop"
    stock = config.stocks["cottages"]
    config.stocks["herbal-workshop"] = {
        key: stock.pop(key) for key in ("herbs", "tea", "manure", "herbal-remedy") if key in stock
    }
    for key in ("plant-scraps", "compost-material", "compost"):
        if key in stock:
            config.stocks["herbal-workshop"][key] = 0
    if config.economy and "cottages" in config.economy.reserve_targets:
        config.economy.reserve_targets["herbal-workshop"] = config.economy.reserve_targets.pop("cottages")
    if config.social:
        for activity in config.social.activities.values():
            if activity.location == "cottages" and (activity.project == "iona-herbal-book"
                    or "plant-notes" in activity.equipment
                    or activity.recipe in {"herb_garden", "herbal_remedy"}):
                activity.location = "herbal-workshop"
    if config.firewood:
        config.firewood.household_location = "cottages"
        for members in config.firewood.households.values():
            if "iona" in members:
                members.remove("iona")
    scenario.opening_narrative = scenario.opening_narrative.replace(
        "All twelve villagers are asleep in their homes along the cottage row, including Elsie.",
        "The villagers are asleep at home, including Iona in her herbal workshop and Elsie at the tavern.")


def build_instant_scenario(base: Scenario) -> Scenario:
    if base.id != "millhaven-living" or not base.village or not base.village.communication:
        raise ValueError("Instant Millhaven requires the existing living scenario with communication enabled")
    scenario = base.model_copy(deep=True)
    scenario.id = "millhaven-instant"
    scenario.title = "Millhaven VII - Tin Cans and String Test"
    scenario.village.communication.instant_messages = True
    depot = village_depot.DEPOT
    scenario.entities.update({
        depot: Entity(name="Communal granary and supply depot", kind="location", village_road=True,
                      description="Dry storage for physically delivered grain, flour, timber, firewood and hay."),
        "square-depot": Entity(name="Depot approach", kind="door", location="village-square",
                               destination=depot, open=True, village_road=True,
                               description="A short lane from the square to the communal granary."),
        "depot-ledger": Entity(name="Communal depot ledger", kind="item", location=depot, takable=False,
                               description="A fixed ledger for the shared depot's owner-accounted stock.",
                               readable_text="Check the local owner ledger before withdrawing supplies. Deposits "
                               "must be carried here; purchases are paid on delivery to your workplace."),
        "depot-woodshed": Entity(name="Depot woodshed", kind="fixture", location=depot,
                                 description=f"Sheltered storage for up to {village_depot.FUEL_CAPACITY} units of firewood."),
    })
    scenario.village.stocks[depot] = dict.fromkeys(sorted(village_depot.PRODUCTS), 0)
    tavern = scenario.entities["tavern"]
    tavern.name = "Elsie's tavern and communal kitchen"
    tavern.description += " Elsie runs the communal kitchen here, cooking and serving meals from delivered supplies."
    scenario.entities["tavern-hearth"].name = "Communal kitchen hearth"
    scenario.opening_narrative = (
        "This test version of Millhaven connects every location with tin cans and taut string. "
        "Requests and replies reach named residents instantly through the line. "
        "They must still accept requests and perform all work, travel and deliveries physically. "
        "The communal granary by the square starts empty; Elsie's tavern houses the communal kitchen. "
        "Remote stock and whereabouts are not shared. " + scenario.opening_narrative.replace(
            "Buyers may post one funded supply request at the physical square board or "
            "write at the supplier's physical workplace inbox. Only actual readers or listeners learn it; "
            "replies and project appointments need real channels. ",
            "Buyers may speak funded supply requests and project appointments into their tin cans. ")
    )
    for agent in scenario.agents:
        agent.role = agent.role.replace(
            "Use the shared physical communication policy: one board or workplace-inbox "
            "request per need, personally learned replies, relevant local sharing, and useful work "
            "while waiting. In-person ordering remains possible, but do not chase absent suppliers "
            "repeatedly. No remote messages or knowledge of anyone's current whereabouts.",
            "Use the tin-can-and-string phone network for named residents, one request per need. "
            "Messages do not reveal remote stock or whereabouts. Keep working while awaiting consent."
        ).replace(
            "use a physical board, inbox or conversation to request funded deliveries",
            "send a tin-can request for funded deliveries"
        ).replace("through a valid physical channel", "through a tin-can request")
    return Scenario.model_validate(scenario.model_dump(mode="json"))


def build_generation_17(complete: Scenario, living: Scenario) -> Scenario:
    if complete.id != "millhaven-complete" or living.id != "millhaven-living":
        raise ValueError("Generation 17 requires the complete and living Millhaven scenario inputs")
    scenario = complete.model_copy(deep=True)
    latest = living.model_copy(deep=True)
    config = scenario.village
    latest_config = latest.village
    if config is None or latest_config is None:
        raise ValueError("Generation 17 requires configured village scenarios")

    scenario.id = "millhaven-generation-17"
    scenario.title = "Millhaven VIII - The Complete Village"
    for field in ("economy", "social", "events", "firewood", "communication", "research", "equipment"):
        if getattr(latest_config, field) is not None:
            setattr(config, field, getattr(latest_config, field).model_copy(deep=True))
    if latest_config.maintenance is not None:
        if config.maintenance is None:
            config.maintenance = latest_config.maintenance.model_copy(deep=True)
        else:
            config.maintenance.tasks.update(latest_config.maintenance.tasks)
    config.spoil_after.update(latest_config.spoil_after)
    config.prices.update(latest_config.prices)
    for site, stock in latest_config.stocks.items():
        config.stocks.setdefault(site, {}).update(stock)
    for field in ("breakfast", "event_driven_waits", "anti_idle", "purposeful_downtime",
                  "require_tea_use", "require_manure_use", "demand_led_completion"):
        setattr(config, field, getattr(latest_config, field))

    for entity_id, entity in latest.entities.items():
        if entity_id not in scenario.entities:
            scenario.entities[entity_id] = entity.model_copy(deep=True)
    village_mining.install_scenario(scenario)
    scenario.entities["north-fields"] = Entity(
        name="North Fields", kind="location", village_road=True,
        description="Open working fields north of Ada's farm. Crops require a future enabled field-work system; travel and meetings are available now.")
    scenario.entities["farm-north-fields"] = Entity(
        name="North field lane", kind="door", location="farm", destination="north-fields", open=True,
        village_road=True, description="A short lane from Ada's farm into the North Fields.")
    scenario.entities["north-pasture"] = Entity(
        name="North Pasture", kind="location", village_road=True,
        description="Fenced grazing and pastureland north of the farm. No automatic grazing or stock movement is implied.")
    scenario.entities["fields-pasture"] = Entity(
        name="Pasture gate", kind="door", location="north-fields", destination="north-pasture", open=True,
        village_road=True, description="A field gate connecting North Fields and North Pasture.")
    cistern = scenario.entities.get("farm-cistern")
    if cistern is None:
        raise ValueError("Generation 17 requires the resilient farm cistern fixture")
    cistern.kind = "location"
    cistern.name = "Emergency Cistern"
    cistern.description = (
        "Covered emergency rainwater storage beside the farm. The site is traversable and inspectable, "
        "but it supplies no usable water until filling and withdrawal rules are implemented.")
    cistern.location = None
    cistern.village_road = True
    cistern.health = None
    cistern.max_health = None
    scenario.entities["farm-emergency-cistern"] = Entity(
        name="Cistern path", kind="door", location="farm", destination="farm-cistern", open=True,
        village_road=True, description="A short marked path from Ada's farm to the emergency cistern.")
    depot = scenario.entities[village_depot.DEPOT]
    depot.name = "Supply Depot"
    depot.description = (
        "Covered storage beside the communal granary for physically delivered timber and firewood. "
        "Food and hay are stored in the separate granary.")
    scenario.entities[village_depot.GRANARY] = Entity(
        name="Communal Granary", kind="location", village_road=True,
        description="Dry communal storage for physically delivered grain, flour and hay.")
    scenario.entities["square-granary"] = Entity(
        name="Granary approach", kind="door", location="village-square",
        destination=village_depot.GRANARY, open=True, village_road=True,
        description="A short lane from the village square to the communal granary.")
    config.stocks[village_depot.GRANARY] = {
        product: config.stocks[village_depot.DEPOT].get(product, 0)
        for product in sorted(village_depot.GRANARY_PRODUCTS)
    }
    config.stocks[village_depot.DEPOT] = {
        product: config.stocks[village_depot.DEPOT].get(product, 0)
        for product in sorted(village_depot.DEPOT_PRODUCTS)
    }
    scenario.entities["communal-kitchen"] = Entity(
        name="Communal Kitchen", kind="location", village_road=True,
        description=("Elsie's physical kitchen beside the tavern for village meal preparation. "
                     "Current meal accounting remains at the tavern until kitchen stock migration is implemented."))
    scenario.entities["tavern-kitchen"] = Entity(
        name="Kitchen passage", kind="door", location="tavern", destination="communal-kitchen", open=True,
        village_road=True, description="An interior passage from the tavern to the communal kitchen.")
    scenario.agents = latest.agents
    stock_reserves = {
        "watermill": {"flour": 8, "grain": 8},
        "farm": {"grain": 10},
        "herbal-workshop": {"herbs": 8},
        "riverbank": {"preserved-fish": 3},
        "woods": {"logs": 10, "timber": 6},
    }
    stock_capacities = {
        "watermill": {"flour": 16, "grain": 16},
        "farm": {"grain": 20},
        "herbal-workshop": {"herbs": 16},
        "riverbank": {"preserved-fish": 6},
        "woods": {"logs": 20, "timber": 12},
        "general-store": {"grain": 8, "flour": 6, "bread": 8, "herbs": 6,
                          "preserved-fish": 4, "furniture": 2, "tackle": 3,
                          "wooden-cup": 4, "herbal-remedy": 3},
    }
    config.economy.reserve_targets.update(stock_reserves)
    config.economy.stock_capacities = stock_capacities
    config.economy.supplier_alternatives.update({
        product: ["nella"] for product in ("grain", "flour", "bread", "herbs", "preserved-fish")
    })
    config.stocks["riverbank"]["preserved-fish"] = config.stocks["riverbank"].get("preserved-fish", 0)
    config.stocks["general-store"].update({product: config.stocks["general-store"].get(product, 0)
                                            for product in stock_capacities["general-store"]})
    config.economy.production_targets.setdefault("woods", {})["firewood"] = 6
    for product, reserve in {"bread": 4, "grain": 4, "flour": 3,
                             "herbs": 3, "preserved-fish": 2}.items():
        config.economy.reserve_targets.setdefault("general-store", {})[product] = reserve
    config.economy.recipes["preserved-fish"] = config.economy.recipes["fish"].model_copy(deep=True)
    config.economy.recipes["preserved-fish"].inputs = {"preserved-fish": 1}
    config.economy.suppliers["preserved-fish"] = "finn"
    config.economy.suppliers.update({"furniture": "hobb", "tackle": "finn",
                                     "wooden-cup": "hobb", "herbal-remedy": "iona"})
    for product in ("preserved-fish", "furniture", "tackle", "wooden-cup", "herbal-remedy"):
        config.economy.reserve_targets.setdefault("general-store", {})[product] = 1
    scenario.objective = latest.objective + (
        " Preserve the village's inherited family history, readable chronicle and optional same-scenario "
        "midnight generation handoff while using the communal depot and feed store physically."
    )
    scenario.opening_narrative = latest.opening_narrative + (
        " Families have worked here before this day. The square's chronicle preserves their stories, "
        "and the communal depot and farm feed store provide finite physical storage for the village."
    )
    return Scenario.model_validate(scenario.model_dump(mode="json"))


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Generate an opt-in living Millhaven scenario; never alter a saved adventure.")
    parser.add_argument("--base", type=Path, default=root / "data" / "scenarios" / "millhaven-expanded.json")
    parser.add_argument("--output", type=Path, default=root / "data" / "scenarios" / "millhaven-living.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--anti-idle", action="store_true", help="Enable Phase 1 fallback planning and anti-idle guards.")
    parser.add_argument("--purposeful-downtime", action="store_true", help="Opt in purposeful planning, optional crafts and a scheduled merchant.")
    parser.add_argument("--firewood", action="store_true", help="Require real firewood for shared cottage fires, ovens and forge work.")
    parser.add_argument("--instant-from-living", action="store_true",
                        help="Derive the tin-cans-and-string test scenario from --base living JSON.")
    parser.add_argument("--generation-17", action="store_true",
                        help="Merge --base complete and --living inputs into the cumulative Generation 17 scenario.")
    parser.add_argument("--living", type=Path, default=root / "data" / "scenarios" / "millhaven-living.json",
                        help="Current Living Millhaven input used by --generation-17.")
    args = parser.parse_args()
    if (args.instant_from_living or args.generation_17) and args.output.resolve() == args.base.resolve():
        parser.error("Generated scenario output must differ from its base")
    if args.instant_from_living and args.generation_17:
        parser.error("Choose either --instant-from-living or --generation-17")
    if args.output.exists() and not args.force:
        parser.error("Output already exists; use --force only to regenerate this scenario")
    base = Scenario.model_validate(read_json(args.base))
    if args.generation_17:
        living = Scenario.model_validate(read_json(args.living))
        scenario = build_generation_17(base, living)
    else:
        scenario = (build_instant_scenario(base) if args.instant_from_living else
                    build_scenario(base, anti_idle=args.anti_idle,
                                   firewood=args.firewood, purposeful_downtime=args.purposeful_downtime))
    write_json(args.output, scenario.model_dump(mode="json"))
    print(f"Generated {scenario.id}: {args.output}")


if __name__ == "__main__":
    main()
