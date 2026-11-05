import math
import random
import secrets
from typing import TYPE_CHECKING, Any

from npc_sim.models import BBQConfig, BBQReading, BBQState

if TYPE_CHECKING:
    from npc_sim.world import World


STEP_SECONDS = 30


def initial_state(config: BBQConfig) -> BBQState:
    seed = secrets.randbits(32)
    return BBQState(seed=seed, temperature_f=config.target_f,
                    heat_percent=(config.target_f - 75) / 3,
                    next_flare_seconds=random.Random(seed).randint(180, 300),
                    history=[BBQReading(seconds=0, temperature_f=config.target_f,
                                        heat_percent=(config.target_f - 75) / 3)])


def context(world: "World") -> tuple[BBQConfig, BBQState]:
    if world.scenario.bbq is None or world.state.bbq is None:
        raise ValueError("Missing BBQ configuration or persistent state")
    return world.scenario.bbq, world.state.bbq


def summary(world: "World") -> dict[str, Any]:
    config, state = context(world)
    complete = state.elapsed_seconds >= config.duration_seconds
    fraction = state.in_band_seconds / state.elapsed_seconds if state.elapsed_seconds else 0
    in_band = abs(state.temperature_f - config.target_f) <= config.tolerance_f
    passed = fraction >= config.minimum_in_band_fraction and in_band
    return {
        "target_f": config.target_f, "tolerance_f": config.tolerance_f,
        "duration_seconds": config.duration_seconds, "elapsed_seconds": state.elapsed_seconds,
        "temperature_f": round(state.temperature_f, 1), "heat_percent": state.heat_percent,
        "in_band_seconds": state.in_band_seconds, "in_band_percent": round(100 * fraction, 1),
        "minimum_in_band_percent": config.minimum_in_band_fraction * 100,
        "in_band": in_band, "flare_active": state.flare_remaining_seconds > 0,
        "flare_count": state.flare_count, "max_deviation_f": round(state.max_deviation_f, 1),
        "status": ("passed" if passed else "failed") if complete else "tending",
        "history": [reading.model_dump() for reading in state.history],
    }


def observation(world: "World") -> dict[str, Any]:
    if world.scenario.bbq is None:
        return {}
    config, state = context(world)
    result = summary(world)
    text = (f"BBQ gauge: {state.temperature_f:.1f} F; target {config.target_f:g} F "
            f"(acceptable band +/-{config.tolerance_f:g} F). Heat dial: {state.heat_percent:g}%. "
            f"Elapsed: {state.elapsed_seconds} of {config.duration_seconds} simulated seconds. "
            f"Time in band: {result['in_band_percent']:g}%; required: "
            f"{result['minimum_in_band_percent']:g}%, with a final reading inside the band. ")
    text += ("A brief grease flare-up has spiked the temperature. " if result["flare_active"]
             else "No active flare-up. ")
    if result["status"] == "tending":
        text += ("Adjust the heat dial (0-100%) or monitor at the current setting. "
                 "Each action advances 30 simulated seconds; model thinking and observation do not advance time. "
                 "Higher heat raises the temperature, lower heat lets it cool. Temperature responds gradually. "
                 "Around 50% normally sustains 225 F; flare-ups add brief heat spikes, then subside. "
                 "Watch the recent readings and restore heat after cooling to avoid undershooting. "
                 "No new flare-ups occur during the final two minutes, allowing time to settle.")
    else:
        text += f"The tending session is complete: {result['status']}."
    opening = state.elapsed_seconds == 0
    if opening and world.scenario.opening_narrative:
        text = world.scenario.opening_narrative + "\n\n" + text
    return {"bbq": result, "opening": opening, "narrative": text}


def actions(world: "World") -> list[dict[str, Any]]:
    config, state = context(world)
    if state.elapsed_seconds >= config.duration_seconds:
        return []
    return [
        {"id": "adjust_bbq_heat",
         "description": "Set the BBQ heat dial to an absolute percentage (0 off, 100 maximum), then let 30 simulated seconds pass and read the gauge. Heat changes gradually; flare-ups subside.",
         "arguments_schema": {"type": "object", "properties": {
             "heat_percent": {"type": "number", "minimum": 0, "maximum": 100}},
             "required": ["heat_percent"], "additionalProperties": False},
         "targets": [], "availability": "available", "reason": None},
        {"id": "monitor_bbq",
         "description": "Leave the heat dial unchanged for 30 simulated seconds, then read the BBQ temperature gauge.",
         "arguments_schema": {"type": "object", "properties": {}, "required": [],
                              "additionalProperties": False},
         "targets": [], "availability": "available", "reason": None},
    ]


def apply(world: "World", heat_percent: float | None) -> tuple[str, str, str, None]:
    config, state = context(world)
    if heat_percent is not None:
        if not math.isfinite(heat_percent):
            return "REJECTED", "INVALID_ARGUMENTS", "Heat percentage must be finite.", None
        state.heat_percent = heat_percent
    flares_before = state.flare_count
    started = state.elapsed_seconds
    end = min(config.duration_seconds, state.elapsed_seconds + STEP_SECONDS)
    while state.elapsed_seconds < end:
        state.elapsed_seconds += 1
        state.flare_remaining_seconds = max(0, state.flare_remaining_seconds - 1)
        if (state.elapsed_seconds >= state.next_flare_seconds
                and state.elapsed_seconds <= config.duration_seconds - 120):
            rng = random.Random(f"{state.seed}:{state.flare_count}")
            state.temperature_f += rng.uniform(25, 40)
            state.flare_remaining_seconds = 30
            state.flare_count += 1
            state.next_flare_seconds = state.elapsed_seconds + rng.randint(240, 420)
        equilibrium = 75 + 3 * state.heat_percent + 5 * math.sin(state.elapsed_seconds / 100)
        state.temperature_f += (equilibrium - state.temperature_f) * (1 - math.exp(-1 / 90))
        deviation = abs(state.temperature_f - config.target_f)
        state.max_deviation_f = max(state.max_deviation_f, deviation)
        if deviation <= config.tolerance_f:
            state.in_band_seconds += 1
    state.history.append(BBQReading(seconds=state.elapsed_seconds,
                                    temperature_f=round(state.temperature_f, 1),
                                    heat_percent=state.heat_percent))
    text = (f"You {'set' if heat_percent is not None else 'kept'} the BBQ heat dial at "
            f"{state.heat_percent:g}% and monitored for {end - started} simulated seconds. "
            f"The gauge now reads {state.temperature_f:.1f} F (target {config.target_f:g} F).")
    if state.flare_count > flares_before:
        text += " A brief grease flare-up spiked the heat during this interval."
    if end == config.duration_seconds:
        result = summary(world)
        text += f" Session complete: {result['in_band_percent']:g}% of time in band; result: {result['status']}."
    return "APPLIED", "BBQ_TENDED", text, None


def evaluate_goal(world: "World") -> dict[str, Any]:
    result = summary(world)
    status = {"tending": "active", "passed": "achieved", "failed": "failed"}[result["status"]]
    return {"status": status,
            "reason": ("Temperature control missed the required time in band or final temperature."
                       if status == "failed" else ""),
            "evidence": result, "observation_revision": world.revision()}
