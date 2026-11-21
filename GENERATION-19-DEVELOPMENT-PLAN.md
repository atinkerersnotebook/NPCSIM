# Millhaven Generation 19 - Resident Daily Planning

## Scope

Generation 19 makes every resident plan from waking time and replan when a personally learned order or supply response changes. Planning uses only known stock, accepted/proposed orders, configured production recipes, service obligations, real travel time and bounded forecasts. It never creates goods or confirms delivery.

The first executable slice is implemented: wake-time two-day planning traces,
material-change replanning hooks, daily-plan persistence, timeline rendering,
initial supplier assumptions/confirmations and customer-adjustment markers.

## P0: Morning planning loop

1. At wake, each resident records a daily plan with:
   - today's meals, maintenance, work window and existing commitments;
   - owned/customer orders, supplier orders and uncommitted forecast demand;
   - required production and upstream inputs;
   - earliest practical order, production, collection and delivery times;
   - explicit assumptions that upstream suppliers can initially cover forecast demand, pending direct confirmation.
   - at least today and tomorrow in the planning window, with later-day rows included when a 3+ day run is active.
2. A planning trace is a concise rationale, not hidden chain-of-thought. Show inputs, calculations, assumptions, actions and uncertainty in the world timeline.
3. Planning remains local and private. Other residents learn plans only through conversation, notices, inboxes or observed results.

### Acceptance criteria

- Every awake resident receives one Day N planning event before productive work.
- The timeline exposes the trigger, assumptions, arithmetic, requirements, selected tasks and blockers.
- Planning never mutates stock, money, orders or travel state.

## P0: Order confirmation and replanning

1. Forecast demand may seed a provisional upstream request, but the NPC must meet or message the supplier through a real physical channel to confirm quantity, price, deadline and freshness.
2. If a supplier accepts, the receiving resident records confirmed incoming supply and rebuilds production/delivery timing.
3. If a supplier rejects, defers or cannot cover the need, the buyer replans immediately: alternate supplier, reduced batch, later deadline or customer warning.
4. If a producer cannot meet a customer order after checking its own inputs, it tells the customer that the quantity/deadline must change; silence is not acceptance and an unconfirmed forecast is not delivered stock.
5. A new or materially changed order triggers replanning even after the morning plan exists.

### Acceptance criteria

- New supply acceptance/rejection and customer updates emit new planning traces.
- Forecasted flour, eggs, milk, bread, fish, tea, firewood, logs, ore, iron and meals carry confirmation state.
- A downstream rejection propagates upstream and to the affected customer without inventing alternate supply.

## P1: Timeline and operator review

- Add a `Daily planning` timeline cell with trigger, calculations, assumptions, plan and adjustment warning.
- Support filtering and export of planning traces.
- Keep the rationale concise, factual and auditable; no hidden model scratchpad is persisted.

## Execution order

1. Add durable daily-plan state and rationale event schema.
2. Trigger planning at wake and on material order/communication changes.
3. Integrate supplier confirmation, rejection and customer-adjustment paths.
4. Add timeline rendering, filters and focused regressions.
5. Run a three-active-day controlled scenario and verify every resident replans only when materially necessary.

