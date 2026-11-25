import argparse
import asyncio
import itertools
import json
import shutil
import statistics
import time
from pathlib import Path
from uuid import uuid4

from npc_sim.controller import Controller, TERMINAL
from npc_sim.dungeon_master import DungeonMasterConfig
from npc_sim.models import AppConfig, Budgets
from npc_sim.storage import read_json, write_json


FORMATS = ("normal", "compact", "shorthand", "fact-shorthand")
OPTIONS = ("message_deduplicate", "message_relevant_memory", "message_aggregate_events",
           "message_perception_delta")


def combinations() -> list[dict[str, object]]:
    return [
        {"message_format": message_format, **dict(zip(OPTIONS, toggles))}
        for message_format in FORMATS
        for toggles in itertools.product((False, True), repeat=len(OPTIONS))
    ]


def shortlist_combinations() -> list[dict[str, object]]:
    return [
        {"message_format": "normal", "message_deduplicate": False,
         "message_relevant_memory": False, "message_aggregate_events": False,
         "message_perception_delta": False},
        {"message_format": "shorthand", "message_deduplicate": True,
         "message_relevant_memory": False, "message_aggregate_events": False,
         "message_perception_delta": False},
        {"message_format": "shorthand", "message_deduplicate": True,
         "message_relevant_memory": True, "message_aggregate_events": False,
         "message_perception_delta": False},
        {"message_format": "shorthand", "message_deduplicate": True,
         "message_relevant_memory": True, "message_aggregate_events": True,
         "message_perception_delta": True},
        {"message_format": "fact-shorthand", "message_deduplicate": True,
         "message_relevant_memory": True, "message_aggregate_events": True,
         "message_perception_delta": True},
    ]


def summarize(events: list[dict], elapsed: float, status: str | None, run: dict | None) -> dict:
    decisions = [event["payload"] for event in events if event["kind"] == "decision"]
    validations = [event["payload"] for event in events if event["kind"] == "validation"]
    requests = [event["payload"] for event in events if event["kind"] == "proxy_request"]
    results = [event["payload"] for event in events if event["kind"] == "action_result"]
    prompt_tokens = [payload.get("usage", {}).get("prompt_tokens", 0) for payload in decisions]
    total_tokens = [payload.get("usage", {}).get("total_tokens", 0) for payload in decisions]
    valid = sum(payload.get("valid") is True for payload in validations)
    invalid = sum(payload.get("valid") is False for payload in validations)
    applied = sum(payload.get("outcome") == "APPLIED" for payload in results)
    diagnostics = [payload.get("compression", {}) for payload in requests if payload.get("compression")]
    normal_bytes = sum(int(item.get("normal_user_bytes", 0)) for item in diagnostics)
    sent_bytes = sum(int(item.get("sent_user_bytes", 0)) for item in diagnostics)
    achieved = bool(run and run.get("verification", {}).get("status") == "achieved")
    return {
        "status": status,
        "reason": run.get("reason") if run else None,
        "objective_achieved": achieved,
        "actions": run.get("actions", 0) if run else 0,
        "model_calls": run.get("model_calls", 0) if run else 0,
        "valid_decisions": valid,
        "invalid_decisions": invalid,
        "valid_rate": valid / max(1, valid + invalid),
        "applied_actions": applied,
        "action_accuracy": applied / max(1, len(results)),
        "wall_seconds": round(elapsed, 3),
        "mean_decision_latency_ms": round(statistics.mean(
            [float(payload.get("latency_ms", 0)) for payload in decisions]), 3) if decisions else 0.0,
        "median_decision_latency_ms": round(statistics.median(
            [float(payload.get("latency_ms", 0)) for payload in decisions]), 3) if decisions else 0.0,
        "normal_user_bytes": normal_bytes,
        "sent_user_bytes": sent_bytes,
        "byte_reduction": 1 - sent_bytes / max(1, normal_bytes),
        "provider_prompt_tokens": sum(prompt_tokens),
        "provider_total_tokens": sum(total_tokens),
        "fallbacks": sum(bool(item.get("fallback_reason")) for item in diagnostics),
        "facts_encoded": sum(int(item.get("facts_encoded", 0)) for item in diagnostics),
        "tables_encoded": sum(int(item.get("tables_encoded", 0)) for item in diagnostics),
        "deduplicated_values": sum(int(item.get("deduplicated_values", 0)) for item in diagnostics),
        "memory_entries_omitted": sum(int(item.get("memory_entries_omitted", 0)) for item in diagnostics),
        "events_aggregated": sum(int(item.get("events_aggregated", 0)) for item in diagnostics),
        "cache_read_tokens": sum(int(item.get("cache_read_tokens") or 0) for item in diagnostics),
        "uncached_prompt_tokens": sum(int(item.get("uncached_prompt_tokens") or 0) for item in diagnostics),
        "cache_status": ("reported" if any(item.get("cache_status") == "reported" for item in diagnostics)
                         else "unavailable"),
        "cache_hit_ratio": (sum(int(item.get("cache_read_tokens") or 0) for item in diagnostics)
                            / sum(int(item.get("provider_prompt_tokens") or 0) for item in diagnostics))
        if sum(int(item.get("provider_prompt_tokens") or 0) for item in diagnostics) else None,
    }


async def evaluate_combination(root: Path, directory: Path, base: AppConfig, index: int,
                               combination: dict[str, object], scenario: str,
                               max_actions: int, max_seconds: float) -> dict:
    data = directory / f"trial-{index:02}"
    shutil.copytree(root / "data" / "scenarios", data / "scenarios")
    config = base.model_copy(deep=True)
    config.world.scenario = scenario
    config.budgets = Budgets(max_actions=max_actions, max_model_calls=max_actions + 8,
                             max_seconds=max_seconds, repetition_window=4)
    for field, value in combination.items():
        setattr(config.proxy, field, value)
    write_json(data / "config.json", config.model_dump(mode="json"))
    write_json(data / "dungeon-master.json", DungeonMasterConfig(enabled=False).model_dump(mode="json"))
    controller = Controller(root, data, config)
    started = time.monotonic()
    await controller.start_worker()
    try:
        await controller.command("start", {"scenario_id": scenario})
        await controller.command("run")
        while controller.run and (controller.run["status"] not in TERMINAL
                                  or controller.run.get("completion_analysis", {}).get("status") in {"pending", "running"}):
            if controller.run["status"] == "paused" and not controller.in_flight:
                break
            await asyncio.sleep(0.1)
        elapsed = time.monotonic() - started
        run = controller.run
        result = summarize(controller.events.events if controller.events else [], elapsed,
                           run.get("status") if run else None, run)
        result.update({"trial": index, **combination})
        write_json(data / "result.json", result)
        print(json.dumps(result, separators=(",", ":")), flush=True)
        return result
    finally:
        await controller.shutdown(preserve_paused=True)


async def evaluate(args: argparse.Namespace) -> dict:
    root = Path(__file__).resolve().parent.parent
    base = AppConfig.model_validate(read_json(root / "data" / "config.json"))
    base.proxy.model = args.model
    directory = root / "data" / "evaluations" / f"compression-{uuid4().hex}"
    directory.mkdir(parents=True)
    semaphore = asyncio.Semaphore(args.concurrency)
    selected = shortlist_combinations() if args.shortlist else combinations()

    async def run(index: int, combination: dict[str, object]) -> dict:
        async with semaphore:
            return await evaluate_combination(root, directory, base, index, combination,
                                              args.scenario, args.max_actions, args.max_seconds)

    results = await asyncio.gather(*[
        run(index, combination) for index, combination in enumerate(selected, 1)
    ])
    baseline = next(item for item in results if item["message_format"] == "normal"
                    and not any(item[option] for option in OPTIONS))
    baseline_tokens = baseline["provider_prompt_tokens"] or baseline["normal_user_bytes"]
    baseline_latency = baseline["mean_decision_latency_ms"] or 1
    for item in results:
        item["prompt_token_delta_vs_baseline"] = item["provider_prompt_tokens"] - baseline["provider_prompt_tokens"]
        item["prompt_token_reduction_vs_baseline"] = 1 - (
            (item["provider_prompt_tokens"] or item["normal_user_bytes"]) / baseline_tokens)
        item["latency_ratio_vs_baseline"] = item["mean_decision_latency_ms"] / baseline_latency
    report = {
        "schema_version": "1.0",
        "scenario": args.scenario,
        "model": args.model,
        "trials": len(results),
        "trials_per_combination": 1,
        "caveat": "N=1 comparison; use repeated runs before selecting a production default.",
        "results": results,
        "report_directory": str(directory),
    }
    write_json(directory / "report.json", report)
    markdown = [
        "# Compression evaluation",
        "",
        f"- Scenario: `{args.scenario}`",
        f"- Model: `{args.model}`",
        "- Trials: 1 per combination",
        "- Accuracy: authoritative goal achievement plus valid/applied action rates",
        "",
        "| Format | Dedup | Relevant memory | Aggregate events | Achieved | Valid rate | Applied rate | Wall s | Mean decision ms | Prompt tokens | Cached tokens | Bytes saved |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in results:
        markdown.append(
            f"| {item['message_format']} | {str(item['message_deduplicate']).lower()} | "
            f"{str(item['message_relevant_memory']).lower()} | {str(item['message_aggregate_events']).lower()} | "
            f"{str(item['objective_achieved']).lower()} | {item['valid_rate']:.1%} | "
            f"{item['action_accuracy']:.1%} | {item['wall_seconds']:.2f} | "
            f"{item['mean_decision_latency_ms']:.1f} | {item['provider_prompt_tokens']} | "
            f"{item['cache_read_tokens']} | "
            f"{item['byte_reduction']:.1%} |")
    markdown.append("")
    markdown.append(report["caveat"])
    (directory / "report.md").write_text("\n".join(markdown), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate all LLM prompt compression combinations.")
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--scenario", default="escape-room")
    parser.add_argument("--shortlist", action="store_true")
    parser.add_argument("--max-actions", type=int, default=80)
    parser.add_argument("--max-seconds", type=float, default=3000)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 4:
        parser.error("--concurrency must be from 1 to 4")
    report = asyncio.run(evaluate(args))
    print(json.dumps({"report": str(Path(report["report_directory"]) / "report.md"),
                      "trials": report["trials"]}, indent=2))


if __name__ == "__main__":
    main()
