import argparse
from pathlib import Path

from npc_sim import village_economy, village_events, village_social, village_maintenance, village_mining, village_livestock, village_communication, village_firewood
from npc_sim.models import Entity, Scenario, VillageEconomyConfig, VillageEventsConfig, VillageSocialConfig, VillageMaintenanceConfig, VillageResearchConfig
from npc_sim.storage import read_json, write_json


def build_scenario(base: Scenario, *, livestock: bool = True, anti_idle: bool = False, firewood: bool = False,
                   purposeful_downtime: bool = False) -> Scenario:
    scenario = base.model_copy(deep=True)
    if scenario.village is None or not scenario.village.expanded_rules:
        raise ValueError("The living village requires an expanded timed village")
    scenario.id = "millhaven-living"
    scenario.title = "Millhaven Living - Work, Friends and Visitors"
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
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        parser.error("Output already exists; use --force only to regenerate this scenario")
    scenario = build_scenario(Scenario.model_validate(read_json(args.base)), anti_idle=args.anti_idle,
                              firewood=args.firewood, purposeful_downtime=args.purposeful_downtime)
    write_json(args.output, scenario.model_dump(mode="json"))
    print(f"Generated {scenario.id}: {args.output}")


if __name__ == "__main__":
    main()
