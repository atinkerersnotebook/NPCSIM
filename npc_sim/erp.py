import json
from typing import TYPE_CHECKING, Any

from npc_sim.models import ERPConfig, ERPState, ERPTerminalEntry

if TYPE_CHECKING:
    from npc_sim.world import World


HELP = """ERP sandbox terminal (not an operating-system shell).
help                              Show this command reference
queue                             List blocked invoices
list receiving-advice             List warehouse receiving evidence
show <document-id>                Inspect a purchase order, invoice or receiving advice
post-receipt <advice-id> <quantity> Post an approved, unposted advice in full to its PO
match <invoice-id>                Run quantity, supplier, item, price and currency three-way matching
release <invoice-id>              Release the hold only after a successful match
audit                             Show the session audit trail
Policy: receipts require approved evidence for the same PO, supplier and item.
No over-receipts, duplicate postings, document edits, tolerance overrides or payments are permitted.
Amounts are stored as integer cents. Use queue and document inspection to investigate."""


def context(world: "World") -> tuple[ERPConfig, ERPState]:
    if world.scenario.erp is None or world.state.erp is None:
        raise ValueError("Missing ERP configuration or persistent state")
    return world.scenario.erp, world.state.erp


def checks(world: "World") -> dict[str, bool]:
    config, state = context(world)
    po, invoice = config.purchase_order, config.invoice
    advice = {entry.id: entry for entry in config.receiving_advice}
    supported = all(
        document_id in advice and advice[document_id].approved
        and advice[document_id].purchase_order == po.id
        and advice[document_id].supplier == po.supplier
        and advice[document_id].item == po.item
        and quantity == advice[document_id].quantity
        for document_id, quantity in state.posted_receipts.items())
    return {
        "supported_receipts": supported,
        "purchase_order": invoice.purchase_order == po.id,
        "supplier": invoice.supplier == po.supplier,
        "item": invoice.item == po.item,
        "quantity": invoice.quantity == po.quantity == config.initial_received + sum(state.posted_receipts.values()),
        "unit_price": invoice.unit_price_cents == po.unit_price_cents,
        "currency": invoice.currency == po.currency,
    }


def observation(world: "World") -> dict[str, Any]:
    if world.scenario.erp is None:
        return {}
    config, state = context(world)
    last = state.history[-1] if state.history else None
    output = last.output if last else f"AP exception queue: {config.invoice.id} is on hold.\nType help to inspect available commands."
    opening = not state.history
    narrative = (world.scenario.opening_narrative + "\n\n"
                 if opening and world.scenario.opening_narrative else "")
    return {
        "erp": {"prompt": "erp>", "command": last.command if last else "",
                "output": output, "invoice_status": "released" if state.invoice_released else
                "matched" if state.invoice_matched else "on_hold"},
        "opening": opening,
        "narrative": narrative + "ERP workspace. " + output,
    }


def actions(world: "World") -> list[dict[str, Any]]:
    return [{
        "id": "erp_terminal",
        "description": "Execute one ERP sandbox command. Use help for syntax, queue for blocked invoices, "
                       "and show <document-id> to inspect business records. This is not a shell; "
                       "only advertised ERP commands are supported.",
        "arguments_schema": {"type": "object", "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 200, "pattern": r"\S"}},
            "required": ["command"], "additionalProperties": False},
        "targets": [], "availability": "available", "reason": None,
    }]


def apply(world: "World", command: str) -> tuple[str, str, str, None]:
    config, state = context(world)
    po, invoice = config.purchase_order, config.invoice
    parts = command.split()

    def reject(code: str, text: str) -> tuple[str, str, str, None]:
        return "REJECTED", code, text, None

    def success(code: str, output: str) -> tuple[str, str, str, None]:
        state.history.append(ERPTerminalEntry(
            actor_id=world.actor_id, command=command, code=code, output=output))
        return "APPLIED", code, output, None

    if parts == ["help"]:
        return success("ERP_HELP", HELP)
    if parts == ["queue"]:
        status = "released" if state.invoice_released else "matched; hold pending release" if state.invoice_matched else "on hold: receipt quantity mismatch"
        return success("ERP_QUEUE", f"{invoice.id} | PO {invoice.purchase_order} | {invoice.supplier} | {status}")
    if parts == ["list", "receiving-advice"]:
        return success("ERP_ADVICE_LIST", "\n".join(
            f"{advice.id} | PO {advice.purchase_order} | item {advice.item} | "
            f"{'posted' if advice.id in state.posted_receipts else 'unposted'}"
            for advice in config.receiving_advice))
    if len(parts) == 2 and parts[0] == "show":
        document_id = parts[1]
        documents = {document.id: document.model_dump() for document in
                     [po, invoice, *config.receiving_advice]}
        if document_id not in documents:
            return reject("ERP_DOCUMENT_NOT_FOUND", "Unknown document ID. Use queue or list receiving-advice.")
        record = documents[document_id]
        if document_id == po.id:
            record["received_quantity"] = config.initial_received + sum(state.posted_receipts.values())
        if document_id == invoice.id:
            record.update(matched=state.invoice_matched, released=state.invoice_released,
                          total_cents=invoice.quantity * invoice.unit_price_cents)
        if document_id in {advice.id for advice in config.receiving_advice}:
            record["posted_quantity"] = state.posted_receipts.get(document_id, 0)
        return success("ERP_DOCUMENT", json.dumps(record, indent=2))
    if parts == ["audit"]:
        return success("ERP_AUDIT", "\n".join(
            f"{index}. {entry.actor_id}: {entry.command} -> {entry.code}"
            for index, entry in enumerate(state.history, 1)) or "No ERP commands recorded.")
    if len(parts) == 3 and parts[0] == "post-receipt":
        advice = next((entry for entry in config.receiving_advice if entry.id == parts[1]), None)
        if advice is None:
            return reject("ERP_DOCUMENT_NOT_FOUND", "Unknown receiving advice. Use list receiving-advice.")
        if not parts[2].isascii() or not parts[2].isdigit() or int(parts[2]) <= 0:
            return reject("ERP_INVALID_QUANTITY", "Receipt quantity must be a positive whole number.")
        quantity = int(parts[2])
        if state.invoice_released:
            return reject("ERP_INVOICE_RELEASED", "The invoice is released; this case is read-only.")
        if advice.id in state.posted_receipts:
            return reject("ERP_DUPLICATE_RECEIPT", "This warehouse advice has already been posted.")
        if (not advice.approved or advice.purchase_order != po.id or advice.supplier != po.supplier
                or advice.item != po.item or quantity != advice.quantity):
            return reject("ERP_UNSUPPORTED_RECEIPT",
                          "Receipt rejected: use the full approved advice quantity for the same PO, supplier and item.")
        if config.initial_received + sum(state.posted_receipts.values()) + quantity > po.quantity:
            return reject("ERP_OVER_RECEIPT", "Receipt would exceed the purchase order quantity.")
        state.posted_receipts[advice.id] = quantity
        state.invoice_matched = False
        return success("ERP_RECEIPT_POSTED",
                       f"Posted {quantity} units from {advice.id} to {po.id}. Received quantity is now "
                       f"{config.initial_received + sum(state.posted_receipts.values())}. No invoice hold was released.")
    if len(parts) == 2 and parts[0] in {"match", "release"}:
        if parts[1] != invoice.id:
            return reject("ERP_DOCUMENT_NOT_FOUND", "Unknown invoice ID. Use queue.")
        valid = checks(world)
        if not all(valid.values()):
            return reject("ERP_MATCH_FAILED", "Three-way match failed: " +
                          ", ".join(key.replace("_", " ") for key, passed in valid.items() if not passed) +
                          ". No hold was released.")
        if parts[0] == "match":
            if state.invoice_released or state.invoice_matched:
                return "NO_CHANGE", "ERP_ALREADY_MATCHED", "The invoice already has a successful match.", None
            state.invoice_matched = True
            return success("ERP_MATCHED", f"Three-way match passed for {invoice.id}: PO, receipts and invoice agree. Hold pending release.")
        if not state.invoice_matched:
            return reject("ERP_MATCH_REQUIRED", "Run a successful three-way match before releasing the invoice hold.")
        if state.invoice_released:
            return "NO_CHANGE", "ERP_ALREADY_RELEASED", "The invoice hold is already released. No payment was made.", None
        state.invoice_released = True
        return success("ERP_RELEASED", f"Released the receipt-mismatch hold on {invoice.id}. "
                       f"Invoice is ready for the normal payment approval workflow. No payment was made.")
    return reject("ERP_UNKNOWN_COMMAND", "Unknown ERP command or invalid syntax. Use help. Shell commands and overrides are not supported.")


def evaluate_goal(world: "World") -> dict[str, Any]:
    config, state = context(world)
    result = {**checks(world), "matched": state.invoice_matched, "hold_released": state.invoice_released}
    return {"status": "achieved" if all(result.values()) else "active", "reason": "",
            "evidence": {"invoice_id": config.invoice.id, "checks": result,
                         "received_quantity": config.initial_received + sum(state.posted_receipts.values()),
                         "posted_receipts": dict(state.posted_receipts),
                         "audit": [entry.model_dump() for entry in state.history]},
            "observation_revision": world.revision()}
