import argparse
import asyncio
import json

import httpx


async def main() -> None:
    parser = argparse.ArgumentParser(description="Control the running NPCSIM admin service.")
    parser.add_argument("command", choices=["start", "step", "run", "pause", "resume", "reset", "cancel", "assess", "status"])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", help="Scenario ID for start/reset")
    parser.add_argument("--agent", action="append", default=[], metavar="SLOT=PROFILE",
                        help="Assign a profile to a task slot; repeat for every slot on start/reset")
    parser.add_argument("--skill-mode", choices=["scenario", "npc_only"],
                        help="Use scenario-specific profile bodies or only each NPC's base body (may be unsolvable)")
    args = parser.parse_args()
    assignments = {}
    for assignment in args.agent:
        slot, separator, profile = assignment.partition("=")
        if not separator or not slot or not profile or slot in assignments:
            parser.error("--agent requires a unique SLOT=PROFILE assignment")
        assignments[slot] = profile
    if assignments and args.command not in {"start", "reset"}:
        parser.error("--agent is only supported for start/reset")
    if args.skill_mode is not None and args.command not in {"start", "reset"}:
        parser.error("--skill-mode is only supported for start/reset")
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        if args.command == "status":
            response = await client.get(args.url + "/api/state")
        else:
            response = await client.post(args.url + f"/api/control/{args.command}",
                                         json={"scenario_id": args.scenario,
                                               "agent_assignments": assignments or None,
                                               "skill_mode": args.skill_mode})
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
