import argparse
import asyncio
import json
import shutil
from pathlib import Path
from uuid import uuid4

from npc_sim.controller import Controller, TERMINAL
from npc_sim.models import AppConfig, Budgets
from npc_sim.storage import read_json, write_json


def successful_episode(result: dict, budgets: Budgets) -> bool:
    analysis = result.get("completion_analysis") or {}
    verification = analysis.get("final_verification") or {}
    return (result.get("successful") is True and analysis.get("status") == "completed"
            and analysis.get("verdict") == "pass" and verification.get("status") == "achieved"
            and result["actions"] <= budgets.max_actions and result["elapsed_seconds"] <= budgets.max_seconds)


async def evaluate(args: argparse.Namespace) -> dict:
    root = Path(__file__).resolve().parent.parent
    config = AppConfig.model_validate(read_json(root / "data" / "config.json"))
    main_scenario = args.scenario or config.world.scenario
    if args.model:
        config.proxy.model = args.model
    if not config.proxy.model:
        raise ValueError("Select a model in data/config.json or supply --model.")
    directory = root / "data" / "evaluations" / uuid4().hex
    directory.mkdir(parents=True)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def episode(scenario: str, index: int) -> dict:
        async with semaphore:
            data = directory / f"{scenario}-{index:02}"
            shutil.copytree(root / "data" / "scenarios", data / "scenarios")
            episode_config = config.model_copy(deep=True)
            episode_config.world.scenario = scenario
            write_json(data / "config.json", episode_config.model_dump(mode="json"))
            controller = Controller(root, data, episode_config)
            await controller.start_worker()
            try:
                await controller.command("start")
                await controller.command("run")
                while controller.run and (controller.run["status"] not in TERMINAL
                                          or controller.run.get("completion_analysis", {}).get("status") in {"pending", "running"}):
                    await asyncio.sleep(0.2)
                run = controller.run
                if run is None:
                    raise RuntimeError("Evaluation did not create a run")
                result = {
                    "scenario": scenario, "episode": index, "run_id": run["id"],
                    "status": run["status"], "actions": run["actions"],
                    "model_calls": run["model_calls"], "elapsed_seconds": run["elapsed_seconds"],
                    "verification": run.get("verification"), "reason": run["reason"],
                    "successful": run.get("successful"), "completion_analysis": run.get("completion_analysis"),
                }
                write_json(data / "result.json", {"schema_version": "1.0", **result})
                print(json.dumps({key: result[key] for key in
                                  ("scenario", "episode", "status", "actions", "elapsed_seconds")}), flush=True)
                return result
            finally:
                await controller.shutdown()

    jobs = [episode(main_scenario, index) for index in range(1, args.episodes + 1)]
    jobs += [episode("sleepy-room", index) for index in range(1, args.fixture_episodes + 1)]
    results = await asyncio.gather(*jobs)
    summary = []
    for scenario, minimum_rate in ((main_scenario, 0.9), ("sleepy-room", 0.9)):
        group = [result for result in results if result["scenario"] == scenario]
        if not group:
            continue
        successes = sum(successful_episode(result, config.budgets) for result in group)
        summary.append({"scenario": scenario, "episodes": len(group), "successes": successes,
                        "success_rate": successes / len(group), "passed": successes / len(group) >= minimum_rate})
    report = {"schema_version": "1.0", "config": config.model_dump(mode="json"),
              "summary": summary, "episodes": results, "report_directory": str(directory)}
    write_json(directory / "report.json", report)
    print(json.dumps({"summary": summary, "report": str(directory / "report.json")}, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in real-proxy evaluations; consumes model requests.")
    parser.add_argument("--model")
    parser.add_argument("--scenario", help="Main scenario ID; defaults to configuration")
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--fixture-episodes", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if args.episodes < 1 or args.fixture_episodes < 0 or not 1 <= args.concurrency <= 4:
        parser.error("Require episodes >= 1, fixture-episodes >= 0, and concurrency from 1 to 4.")
    report = asyncio.run(evaluate(args))
    if not all(group["passed"] for group in report["summary"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
