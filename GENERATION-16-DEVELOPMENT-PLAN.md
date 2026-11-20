# Millhaven Generation 16 - development plan

## Goal

Make persistent, tiered world assets available in every scenario. An asset may be a
large production machine, village infrastructure, a raised garden bed, boulder,
puzzle, or portable tool such as a saw, axe, or hammer.

## Data model

- `Scenario.equipment` is the scenario-wide opt-in catalog.
- `WorldState.equipment` persists each asset's current tier and adventure effects.
- Village scenarios may instead use `VillageConfig.equipment` and gain ownership,
  funding, materials, scheduled upgrade work, and autonomous investment decisions.
- Every configured asset references an existing entity or location. Portable tools
  retain ordinary inventory and handling behavior.

## Tier effects

Each tier can define neutral interaction `effects` and optional simulation profiles:

- production duration and batch size
- transport or storage capacity
- water capacity
- fuel use
- arbitrary scenario-specific named effects

Equipment never creates goods without real inputs. An upgrade consumes its declared
village stock and coins when the village policy is enabled.

## Generation 16 first slice

Living Millhaven configures the mill drive, bakery oven, smelting furnace, communal
wagon, public pump, and village well. Wagon capacity and production planning use
the active tier. Adventure scenarios can declare tool, obstacle, puzzle, fixture,
and location assets without a village economy.

## Next increments

1. Add raised-bed yield and crop-bed occupancy profiles.
2. Add boulder and puzzle state transitions driven by tier-specific effects.
3. Let skill challenges consume equipment tier facts as prerequisites/effects.
4. Add equipment degradation and tier-specific maintenance intervals.
5. Add village investment ROI scoring from observed demand and capacity bottlenecks.
6. Expose asset configuration in the operator configuration API and UI.
