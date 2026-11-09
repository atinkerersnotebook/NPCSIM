import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from npc_sim.controller import Controller
from npc_sim.models import AgentProfile, AppConfig, BudgetExtension, ClockRequest, EpisodeLoadRequest, EpisodeSaveRequest, NeedToggle, ReopenRequest, RunModeRequest, Scenario, StartRequest, VillageConfigurationRequest
from npc_sim.storage import read_json, write_json
from npc_sim.agents import default_profile_id, load_profiles, save_profile, slots
from npc_sim.dungeon_master import DungeonMasterConfig, load_config as load_dungeon_master
from npc_sim.interactions import ApprovalRequest, ChatRequest, EditRequest


ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("NPCSIM_DATA", str(ROOT / "data"))).resolve()
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = AppConfig.model_validate(read_json(DATA / "config.json"))
    controller = Controller(ROOT, DATA, config)
    app.state.controller = controller
    await controller.start_worker()
    try:
        yield
    finally:
        await controller.shutdown()


app = FastAPI(title="NPCSIM Admin", lifespan=lifespan)


@app.middleware("http")
async def local_admin_only(request: Request, call_next: Any):
    host = request.headers.get("host", "").split(":")[0]
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "Admin API is loopback-only."}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in {"http://" + request.headers.get("host", ""),
                                 "http://127.0.0.1:5173", "http://localhost:5173"}:
        return JSONResponse({"detail": "Untrusted admin origin."}, status_code=403)
    return await call_next(request)


def controller(request: Request) -> Controller:
    return request.app.state.controller


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
async def state(request: Request) -> JSONResponse:
    return JSONResponse(controller(request).status())


@app.get("/api/scenarios")
async def scenarios() -> dict[str, Any]:
    choices = [Scenario.model_validate(read_json(path)) for path in sorted((DATA / "scenarios").glob("*.json"))]
    return {"scenarios": [{"id": choice.id, "title": choice.title, "objective": choice.initial_objective or choice.objective,
                          "agent_slots": [{"id": slot, "profile_id": default_profile_id(choice, slot),
                                           "start_location": next((agent.start_location or choice.start_location
                                                                   for agent in choice.agents if agent.id == slot),
                                                                  choice.start_location)}
                                          for slot in slots(choice)]}
                          for choice in choices]}


@app.get("/api/agents")
async def agents() -> dict[str, Any]:
    try:
        return {"agents": [profile.model_dump(mode="json") for profile in load_profiles(DATA).values()]}
    except ValueError as error:
        LOGGER.warning("Invalid agent configuration: %s", error)
        raise HTTPException(422, str(error)) from error

@app.get("/api/dungeon-master")
async def dungeon_master() -> dict[str, Any]:
    return load_dungeon_master(DATA).model_dump(mode="json")


@app.put("/api/dungeon-master")
async def save_dungeon_master(config: DungeonMasterConfig) -> dict[str, Any]:
    write_json(DATA / "dungeon-master.json", config.model_dump(mode="json"))
    return config.model_dump(mode="json")


@app.put("/api/agents/{agent_id}")
async def save_agent(agent_id: str, profile: AgentProfile) -> dict[str, Any]:
    if agent_id != profile.id:
        raise HTTPException(422, "Agent ID must match the profile ID.")
    if any(path.stem.casefold() == profile.id.casefold() and path.stem != profile.id
           for path in (DATA / "agents").glob("*.json")):
        raise HTTPException(409, "Agent IDs cannot differ only in letter case.")
    try:
        return save_profile(DATA, profile).model_dump(mode="json")
    except ValueError as error:
        LOGGER.warning("Invalid agent configuration: %s", error)
        raise HTTPException(422, str(error)) from error


@app.get("/api/models")
async def models(request: Request) -> dict[str, Any]:
    try:
        return {"models": await controller(request).proxy.models()}
    except Exception as error:
        LOGGER.warning("Model discovery failed: %s", controller(request).safe_error(error))
        raise HTTPException(502, controller(request).safe_error(error)) from error


@app.put("/api/config")
async def configure(config: AppConfig, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("configure", config.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/control/{command}")
async def control(command: str, body: StartRequest, request: Request) -> dict[str, Any]:
    if command not in {"start", "reset", "step", "run", "pause", "resume", "cancel", "assess"}:
        raise HTTPException(404, "Unknown control")
    try:
        return await controller(request).command(command, body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(500, controller(request).safe_error(error)) from error


@app.post("/api/budget/extend")
async def extend_budget(body: BudgetExtension, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("extend_budget", body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(500, controller(request).safe_error(error)) from error


@app.post("/api/adventure/reopen")
async def reopen(body: ReopenRequest, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("reopen", body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(500, controller(request).safe_error(error)) from error


@app.post("/api/adventure/run-mode")
async def run_mode(body: RunModeRequest, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("run_mode", body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(500, controller(request).safe_error(error)) from error


@app.post("/api/village/configure")
async def village_configuration(body: VillageConfigurationRequest, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("configure_village", body.model_dump(mode="json", exclude_none=True))
    except ValueError as error:
        LOGGER.warning("Village configuration rejected: %s", error)
        raise HTTPException(409, str(error)) from error


@app.put("/api/actors/{actor_id}/needs/{need}")
async def need_toggle(actor_id: str, need: str, body: NeedToggle, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("need", {
            "actor_id": actor_id, "need": need, "enabled": body.enabled})
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

@app.post("/api/interactions/{operation}")
async def interact(operation: str, body: ChatRequest | EditRequest | ApprovalRequest, request: Request) -> dict[str, Any]:
    commands = {"chat": ("npc_chat", ChatRequest), "propose": ("propose_edit", EditRequest),
                "approve": ("approve_edit", ApprovalRequest)}
    if operation not in commands:
        raise HTTPException(404, "Unknown interaction.")
    command, expected = commands[operation]
    if not isinstance(body, expected):
        raise HTTPException(422, "Interaction payload does not match the operation.")
    try:
        return await controller(request).command(command, body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        LOGGER.exception("Operator interaction failed")
        raise HTTPException(502, controller(request).safe_error(error)) from error


@app.put("/api/clock")
async def clock_settings(body: ClockRequest, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("clock", body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


def live_event(event: dict[str, Any]) -> dict[str, Any]:
    if event["kind"] not in {"proxy_request", "dungeon_master_request", "assessment_request"}:
        return event
    payload = {key: value for key, value in event["payload"].items() if key != "messages"}
    return {**event, "payload": {**payload, "technical_payload_omitted": True}}


@app.get("/api/events")
async def events(request: Request, after: int = 0, compact: bool = False):
    active = controller(request)
    recorded = active.events.events[max(0, after):] if active.events else []
    payload = {"run_id": active.run["id"] if active.run else None,
               "events": [live_event(event) for event in recorded] if compact else recorded}
    return await asyncio.to_thread(JSONResponse, payload)


@app.get("/api/events/{sequence}")
async def event_detail(sequence: int, run_id: str, request: Request):
    active = controller(request)
    if not active.run or active.run["id"] != run_id:
        raise HTTPException(409, "The adventure changed. Refresh before inspecting an event.")
    if not active.events or not 1 <= sequence <= len(active.events.events):
        raise HTTPException(404, "Unknown event sequence")
    return JSONResponse(active.events.events[sequence - 1])


@app.get("/api/stream")
async def stream(request: Request, run_id: str = "", after: int = 0, compact: bool = False):
    active = controller(request)
    last_id = request.headers.get("last-event-id", f"{run_id}:{max(0, after)}" if run_id else "")

    async def generate():
        run_id, sequence = "", 0
        previous_snapshot = ""
        if ":" in last_id:
            run_id, raw_sequence = last_id.rsplit(":", 1)
            if raw_sequence.isdigit():
                sequence = int(raw_sequence)
        while not await request.is_disconnected():
            current = active.run["id"] if active.run else ""
            if current != run_id:
                run_id, sequence = current, 0
                previous_snapshot = ""
            snapshot = json.dumps(active.status())
            if snapshot != previous_snapshot:
                previous_snapshot = snapshot
                yield f"event: snapshot\ndata: {snapshot}\n\n"
            if active.events:
                for index, event in enumerate(active.events.events[sequence:]):
                    sequence = event["sequence"]
                    yield f"id: {event['id']}\nevent: run-event\ndata: {json.dumps(live_event(event) if compact else event)}\n\n"
                    if index % 20 == 19:
                        await asyncio.sleep(0)
                        if await request.is_disconnected():
                            return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/transcript")
async def transcript(request: Request):
    active = controller(request)
    return JSONResponse({
        "schema_version": "1.0", "run": active.run,
        "events": active.events.events if active.events else [],
    }, headers={"Content-Disposition": 'attachment; filename="npc-transcript.json"'})


@app.get("/api/history")
async def history() -> dict[str, Any]:
    runs = [read_json(path) for path in DATA.glob("runs/*/run.json")]
    return {"runs": sorted(runs, key=lambda run: run["created_at"], reverse=True)}


@app.get("/api/saves")
async def episode_saves() -> dict[str, Any]:
    from npc_sim.episode_saves import list_saves
    try:
        return {"saves": await asyncio.to_thread(list_saves, DATA)}
    except (ValueError, OSError) as error:
        LOGGER.exception("Saved episodes could not be listed")
        raise HTTPException(409, str(error)) from error


@app.post("/api/saves")
async def save_episode(body: EpisodeSaveRequest, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("save_episode", body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(500, controller(request).safe_error(error)) from error


@app.post("/api/saves/load")
async def load_episode(body: EpisodeLoadRequest, request: Request) -> dict[str, Any]:
    try:
        return await controller(request).command("load_episode", body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(500, controller(request).safe_error(error)) from error


@app.get("/api/history/{run_id}")
async def historical_run(run_id: str):
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        raise HTTPException(400, "Invalid run ID")
    directory = DATA / "runs" / run_id
    if not (directory / "run.json").exists():
        raise HTTPException(404, "Run not found")
    return {"run": read_json(directory / "run.json"),
            "events": [read_json(path) for path in sorted((directory / "events").glob("*.json"))]}


DIST = ROOT / "ui" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/")
async def dashboard():
    if not (DIST / "index.html").exists():
        raise HTTPException(503, "Build the TypeScript UI: cd ui; npm install; npm run build")
    return FileResponse(DIST / "index.html")
