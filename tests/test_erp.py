from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from npc_sim.contracts import action_result, goal_result, observation
from npc_sim.controller import Controller
from npc_sim.models import Scenario
from npc_sim.storage import read_json
from test_controller import TestProxy, configuration
from npc_sim.world import World


ROOT = Path(__file__).resolve().parent.parent


def scenario():
    return Scenario.model_validate(read_json(ROOT / "data" / "scenarios" / "erp-invoice-exception.json"))


def world(tmp_path):
    return World(scenario(), tmp_path / "world.json", "erp", "npc", "session")


def command(world, text, request_id=None):
    rid = request_id or uuid4().hex
    return action_result(world.act(rid, world.revision(), "erp_terminal", {"command": text}),
                         rid, world.session_id)


def test_terminal_only_observation_and_document_discovery(tmp_path):
    w = world(tmp_path)
    obs = observation(w.observe())
    assert obs["self"]["inventory"] == obs["self"]["needs"] == []
    assert [action["id"] for action in obs["actions"]] == ["erp_terminal"]
    assert "ASN-8831" not in str(obs)
    assert "100" not in obs["erp"]["output"]
    assert goal_result(w.evaluate_goal("erp_invoice_released"))["status"] == "active"
    assert "post-receipt" in command(w, "help")["explanation"]
    assert "INV-1042" in command(w, "queue")["explanation"]
    assert "ASN-8831" in command(w, "list receiving-advice")["explanation"]
    assert '"received_quantity": 80' in command(w, "show PO-45017")["explanation"]
    assert '"total_cents": 250000' in command(w, "show INV-1042")["explanation"]
    assert '"approved": true' in command(w, "show ASN-8831")["explanation"]
    assert '"purchase_order": "PO-45018"' in command(w, "show ASN-8832")["explanation"]
    assert '"approved": false' in command(w, "show ASN-8833")["explanation"]


def test_receipt_match_release_persistence_and_audit(tmp_path):
    w = world(tmp_path)
    assert command(w, "match INV-1042")["code"] == "ERP_MATCH_FAILED"
    assert command(w, "release INV-1042")["code"] == "ERP_MATCH_FAILED"
    assert command(w, "post-receipt ASN-8831 20")["code"] == "ERP_RECEIPT_POSTED"
    assert w.evaluate_goal("erp_invoice_released")["status"] == "active"
    assert command(w, "release INV-1042")["code"] == "ERP_MATCH_REQUIRED"
    assert command(w, "match INV-1042")["code"] == "ERP_MATCHED"
    assert w.evaluate_goal("erp_invoice_released")["status"] == "active"
    assert command(w, "match INV-1042")["outcome"] == "NO_CHANGE"
    w = world(tmp_path)
    assert w.state.erp.invoice_matched
    assert command(w, "release INV-1042")["code"] == "ERP_RELEASED"
    result = goal_result(w.evaluate_goal("erp_invoice_released"))
    assert result["status"] == "achieved"
    assert result["evidence"]["received_quantity"] == 100
    assert all(result["evidence"]["checks"].values())
    assert [entry["code"] for entry in result["evidence"]["audit"]] == [
        "ERP_RECEIPT_POSTED", "ERP_MATCHED", "ERP_RELEASED"]
    assert world(tmp_path).state.erp == w.state.erp
    assert world(tmp_path).observe()["erp"]["invoice_status"] == "released"
    assert "No payment was made" in command(w, "release INV-1042")["explanation"]
    assert "ERP_RELEASED" in command(w, "audit")["explanation"]


@pytest.mark.parametrize(("text", "code"), [
    ("post-receipt ASN-8832 20", "ERP_UNSUPPORTED_RECEIPT"),
    ("post-receipt ASN-8833 20", "ERP_UNSUPPORTED_RECEIPT"),
    ("post-receipt ASN-8831 19", "ERP_UNSUPPORTED_RECEIPT"),
    ("post-receipt ASN-8831 21", "ERP_UNSUPPORTED_RECEIPT"),
    ("post-receipt ASN-8831 -1", "ERP_INVALID_QUANTITY"),
    ("post-receipt ASN-8831 0", "ERP_INVALID_QUANTITY"),
    ("post-receipt ASN-8831 20.0", "ERP_INVALID_QUANTITY"),
    ("post-receipt ASN-8831 \u00b2", "ERP_INVALID_QUANTITY"),
    ("post-receipt NO-SUCH-ID 20", "ERP_DOCUMENT_NOT_FOUND"),
    ("show NO-SUCH-ID", "ERP_DOCUMENT_NOT_FOUND"),
    ("match NO-SUCH-ID", "ERP_DOCUMENT_NOT_FOUND"),
    ("release NO-SUCH-ID", "ERP_DOCUMENT_NOT_FOUND"),
    ("pay INV-1042", "ERP_UNKNOWN_COMMAND"),
    ("release INV-1042 --force", "ERP_UNKNOWN_COMMAND"),
    ("help; whoami", "ERP_UNKNOWN_COMMAND"),
    ("show INV-1042\nrelease INV-1042", "ERP_UNKNOWN_COMMAND"),
])
def test_rejected_commands_do_not_mutate_business_state(tmp_path, text, code):
    w = world(tmp_path)
    before = w.state.erp.model_copy(deep=True)
    revision = w.revision()
    result = command(w, text)
    assert result["outcome"] == "REJECTED"
    assert result["code"] == code
    assert result["explanation"]
    assert w.state.erp == before
    assert w.revision() == revision


def test_idempotent_posting_stale_observations_and_duplicate_protection(tmp_path):
    w = world(tmp_path)
    revision, rid = w.revision(), uuid4().hex
    result = command(w, "post-receipt ASN-8831 20", rid)
    assert w.act(rid, revision, "erp_terminal", {"command": "post-receipt ASN-8831 20"}) == result
    assert command(w, "post-receipt ASN-8831 20")["code"] == "ERP_DUPLICATE_RECEIPT"
    assert w.act(uuid4().hex, revision, "erp_terminal", {"command": "match INV-1042"})["code"] == "STALE_OBSERVATION"
    assert w.act(rid, revision, "erp_terminal", {"command": "queue"})["code"] == "REQUEST_ID_CONFLICT"
    restored = world(tmp_path)
    assert restored.act(rid, revision, "erp_terminal", {"command": "post-receipt ASN-8831 20"}) == result
    assert restored.state.erp.posted_receipts == {"ASN-8831": 20}
    assert len(restored.state.erp.history) == 1


@pytest.mark.parametrize(("field", "value"), [
    ("supplier", "Different supplier"), ("item", "OTHER-ITEM"),
    ("unit_price_cents", 2600), ("currency", "EUR"), ("quantity", 99),
])
def test_matching_checks_business_fields_not_just_receipt_quantity(tmp_path, field, value):
    raw = scenario().model_dump()
    raw["erp"]["invoice"][field] = value
    w = World(Scenario.model_validate(raw), tmp_path / "world.json", "erp", "npc", "session")
    command(w, "post-receipt ASN-8831 20")
    assert command(w, "match INV-1042")["code"] == "ERP_MATCH_FAILED"
    assert command(w, "release INV-1042")["code"] == "ERP_MATCH_FAILED"
    assert not w.state.erp.invoice_released


def test_over_receipts_and_wrong_supplier_or_item_evidence(tmp_path):
    raw = scenario().model_dump()
    raw["erp"]["initial_received"] = 90
    w = World(Scenario.model_validate(raw), tmp_path / "world.json", "erp", "npc", "session")
    assert command(w, "post-receipt ASN-8831 20")["code"] == "ERP_OVER_RECEIPT"
    for field in ("supplier", "item"):
        w.scenario.erp.receiving_advice[1] = scenario().erp.receiving_advice[1].model_copy(update={field: "OTHER"})
        assert command(w, "post-receipt ASN-8831 20")["code"] == "ERP_UNSUPPORTED_RECEIPT"


@pytest.mark.parametrize("arguments", [{}, {"command": ""}, {"command": " "}, {"command": 1},
                                        {"command": "queue", "force": True}])
def test_invalid_action_arguments(tmp_path, arguments):
    w = world(tmp_path)
    assert w.act(uuid4().hex, w.revision(), "erp_terminal", arguments)["code"] == "INVALID_ARGUMENTS"
    assert w.state.erp.history == []


@pytest.mark.parametrize("change", [
    {"erp": None}, {"goal_type": "actor_at_exit_location"},
    {"body": {"description": "Physical actor", "capabilities": ["manipulate"], "capacity": 1}},
    {"agents": [{"id": "analyst", "role": "Analyst"}]},
    {"recipe_actions": {"erp_terminal": "Override"}},
])
def test_invalid_scenario_configuration(change):
    raw = scenario().model_dump()
    raw.update(change)
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


def test_invalid_document_configuration_and_missing_saved_state(tmp_path):
    raw = scenario().model_dump()
    raw["erp"]["initial_received"] = 101
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)
    raw = scenario().model_dump()
    raw["erp"]["receiving_advice"][0]["id"] = "INV-1042"
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)
    w = world(tmp_path)
    w.state.erp = None
    w.save()
    with pytest.raises(ValueError, match="missing its persistent ERP state"):
        world(tmp_path)


@pytest.mark.asyncio
async def test_controller_solves_erp_case_over_mcp_and_archives_evidence(configuration):
    data, config = configuration
    config.world.scenario = "erp-invoice-exception"
    controller = Controller(ROOT, data, config)
    commands = ["help", "queue", "show INV-1042", "show PO-45017", "list receiving-advice",
                "show ASN-8832", "show ASN-8833", "show ASN-8831",
                "post-receipt ASN-8831 20", "match INV-1042", "release INV-1042"]
    proxy = TestProxy(controller.proxy, [("erp_terminal", {"command": text}) for text in commands])
    controller.proxy = proxy
    await controller.start_worker()
    try:
        await controller.command("start")
        for _ in commands:
            await controller.command("step")
        assert controller.run["status"] == "succeeded"
        assert controller.run["successful"] is True
        assert controller.run["verification"]["evidence"]["checks"]["hold_released"]
        saved = read_json(data / "runs" / controller.run["id"] / "run.json")
        assert saved["successful"] is True
        assert "erp" in proxy.contexts[-1]["observation"]
        assert "Released the receipt-mismatch hold" in str(saved["completion_analysis"]["story"])
    finally:
        await controller.shutdown()
