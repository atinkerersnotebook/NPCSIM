import { z } from "zod";

const record = z.record(z.string(), z.unknown());
export const secondaryConditionSchema = z.strictObject({
  kind: z.enum(["discovered", "carrying", "at_location", "item_at", "opened", "unlocked", "read"]),
  target: z.string().min(1), actor_id: z.string().nullable().default(null),
  quantifier: z.enum(["any", "all"]).default("any"), location_id: z.string().nullable().default(null)
});
export const secondaryGoalSpecSchema = z.strictObject({
  title: z.string().min(1), description: z.string().min(1),
  assigned_actor_ids: z.array(z.string()).min(1),
  conditions: z.array(secondaryConditionSchema).min(1)
});
export const secondaryGoalSchema = secondaryGoalSpecSchema.extend({
  status: z.enum(["active", "achieved"]),
  condition_met: z.array(z.boolean()), completed_revision: z.string().nullable()
});
const gripsSchema = z.array(z.enum(["hand", "paw", "claw", "mouth", "beak"]));
const bodySizeSchema = z.enum(["small", "medium", "large"]);
const embodimentFields = {
  size: bodySizeSchema.default("medium"),
  grips: gripsSchema.default(["hand"]),
  max_held_items: z.number().int().nonnegative().nullable().default(null)
};
const bodySchema = z.strictObject({
  description: z.string(), capabilities: z.array(z.string()), capacity: z.number().int().nonnegative(),
  ...embodimentFields
});
const documentNames = [
  "IDENTITY.md", "SOUL.md", "AGENTS.md", "USER.md", "MEMORY.md",
  "ALIGNMENT_SYNTHESIS.md", "PROACTIVE_PREFERENCES.md", "README.md"
];
const documentNameSchema = z.string().refine(name => documentNames.includes(name) ||
  /^people\/[A-Za-z0-9_-]+\.md$/.test(name));
const agentDocumentsSchema = z.record(documentNameSchema, z.string()).refine(documents => {
  const names = Object.keys(documents);
  return new Set(names.map(name => name.toLowerCase())).size === names.length &&
    names.every(name => name.toLowerCase() !== "people/index.md" || name === "people/INDEX.md");
}, { message: "Document names must be distinct; use people/INDEX.md for the relationship index." });
export const agentProfileSchema = z.strictObject({
  schema_version: z.literal("1.0"), id: z.string().regex(/^[A-Za-z0-9_-]+$/),
  name: z.string().min(1), identity: z.string().min(1), personality: z.string().min(1),
  operating_rules: z.array(z.string()).min(1), constraints: z.array(z.string()).min(1),
  body: bodySchema, scenario_bodies: z.record(z.string(), bodySchema),
  documents: agentDocumentsSchema.default({})
});
export const agentsSchema = z.object({ agents: z.array(agentProfileSchema) });
export type AgentProfile = z.infer<typeof agentProfileSchema>;
export const skillModeSchema = z.enum(["scenario", "npc_only"]);
export type SkillMode = z.infer<typeof skillModeSchema>;
export const dungeonMasterConfigSchema = z.strictObject({
  schema_version: z.literal("1.0"), enabled: z.boolean(), name: z.string().min(1),
  identity: z.string(), personality: z.string(),
  capabilities: z.array(z.enum(["observe_team", "adjudicate_actions", "review_progress", "declare_defeat"])),
  operating_rules: z.array(z.string()).min(1), constraints: z.array(z.string()).min(1)
}).refine(value => !value.capabilities.includes("review_progress") || value.capabilities.includes("observe_team"),
  { message: "Progress reviews require observe_team capability" });
export const dungeonMasterRulingSchema = z.object({
  name: z.string(), scope: z.enum(["action", "episode"]),
  verdict: z.enum(["allowed", "rejected", "continue", "defeat"]),
  reason: z.string(), evidence_ids: z.array(z.string()), source: z.enum(["world", "validation", "ai"])
});
export type DungeonMasterConfig = z.infer<typeof dungeonMasterConfigSchema>;
export const clockSchema = z.object({
  seconds: z.number().finite().nonnegative(), real_seconds: z.number().finite().nonnegative(),
  rate: z.number().finite().min(1).max(600), running: z.boolean(),
  mode: z.enum(["decision_paused", "continuous"]).default("continuous")
});
export const breadBatchSchema = z.object({
  ingredients: z.array(z.string()),
  stage: z.enum(["measuring", "mixing", "mixed", "rising", "risen", "proofing", "proofed",
    "baking", "baked", "cooling", "cooled", "served", "ruined"]),
  equipment: z.string().nullable(), ready_at: z.number().nullable(),
  deadline: z.number().nullable(), failure: z.string().nullable()
});
export const nutritionSchema = z.object({
  serving_grams: z.number().finite().nonnegative(), calories_kcal: z.number().finite().nonnegative(),
  protein_g: z.number().finite().nonnegative(), carbohydrates_g: z.number().finite().nonnegative(),
  fat_g: z.number().finite().nonnegative(), fiber_g: z.number().finite().nonnegative(),
  sugar_g: z.number().finite().nonnegative()
});
export type Nutrition = z.infer<typeof nutritionSchema>;
export const completionAnalysisSchema = z.object({
  status: z.enum(["pending", "running", "completed", "error"]),
  verdict: z.enum(["pass", "fail"]).nullable(),
  findings: z.array(z.object({ reason: z.string(), evidence_ids: z.array(z.string()) })),
  story: z.array(z.object({ id: z.string(), text: z.string() })),
  final_verification: record.nullable(), story_review: record.nullable(),
  model: z.string().nullable(), model_calls: z.number(), elapsed_seconds: z.number(),
  completed_at: z.string().nullable(), error: z.string().nullable()
});
export type CompletionAnalysis = z.infer<typeof completionAnalysisSchema>;
const needSchema = z.object({
  id: z.string(), enabled: z.boolean(), level: z.number(),
  scale: z.object({ min: z.number(), max: z.number(), higher_means: z.string() }),
  effects: z.array(z.string())
});
const villageEventSchema = z.object({
  minute: z.number().int().nonnegative(), actor: z.string(), action: z.string(),
  target: z.string(), result: z.string(), witnesses: z.array(z.string())
});
const villageTaskSchema = z.object({
  kind: z.enum(["fetch_shovel", "dig_well", "repair_mill"]),
  assignee: z.string(), status: z.enum(["accepted", "in_progress", "blocked", "completed", "cancelled"]),
  step: z.string(), blocker: z.string().nullable()
});
const villageBeliefSchema = z.object({
  subject: z.string(), location: z.string().nullable(), source: z.string(),
  learned_at: z.number().int().nonnegative(), confidence: z.number().min(0).max(1)
});
const villageRoutineSchema = z.object({ minute: z.number().int(), location: z.string(), activity: z.string() });
const villageJobSchema = z.object({
  location: z.string(), interval: z.number().int().positive(),
  inputs: z.record(z.string(), z.number().int().positive()).default({}),
  outputs: z.record(z.string(), z.number().int().positive()).default({}), equipment: z.string().nullable().default(null),
  duration: z.number().int().positive().default(5), interruptible: z.boolean().default(true),
  batch_size: z.number().int().min(1).max(2).default(1)
});
const villageDeliverySchema = z.object({
  source: z.string(), destination: z.string(), resource: z.string(), seller: z.string(),
  buyer: z.string(), price: z.number().int().positive()
});
const villageResidentSchema = z.object({
  occupation: z.string(), personality: z.string(), skills: z.array(z.string()),
  home: z.string(), work: z.string(), schedule: z.array(villageRoutineSchema),
  job: villageJobSchema.nullable().default(null),
  wake_minute: z.number().int().default(480), bed_minute: z.number().int().default(1320)
});
const villageLifeSchema = z.object({
  hungry: z.boolean(), missed_meals: z.number().int().nonnegative(),
  meals: z.record(z.string(), z.object({
    kind: z.literal("daily"), due: z.number().int().nonnegative(),
    status: z.enum(["pending", "eaten", "missed"]), source: z.string().nullable()
  })),
  sleeping: z.boolean(), sleep_until: z.number().int().nonnegative().nullable(), last_woke: z.number().int(),
  work: z.object({
    location: z.string(), finishes_at: z.number().int().nonnegative(),
    outputs: z.record(z.string(), z.number().int().positive()), equipment: z.string().nullable(),
    kind: z.enum(["production", "wagon_repair"]).default("production")
  }).nullable(),
  fatigue_days: z.array(z.number().int()).optional(), tea_day: z.number().int().optional(),
  urgent_project: z.string().nullable().optional(), urgent_until: z.number().int().nullable().optional()
});
export type VillageLife = z.infer<typeof villageLifeSchema>;
const villageLotSchema = z.object({
  quantity: z.number().int().positive(), produced_at: z.number().int().nonnegative(),
  expires_at: z.number().int().nonnegative().nullable()
});
const villageShipmentSchema = z.object({
  resource: z.string(), quantity: z.number().int().positive(), seller: z.string(), buyer: z.string(),
  destination: z.string(), price: z.number().int().positive(), lots: z.array(villageLotSchema).optional()
});
const villageDeadlineSchema = z.object({
  due: z.number().int().nonnegative(), completed_at: z.number().int().nonnegative().nullable(), overdue: z.boolean()
});
const villageWagonSchema = z.object({
  repaired: z.boolean(), driver: z.string().nullable(), automatic: z.boolean(),
  loads: z.array(villageShipmentSchema), route_index: z.number().int(), phase: z.enum(["travel", "unload", "load"]),
  next_event_at: z.number().int(), blocker: z.string().nullable(), hitched: z.boolean().optional()
});
export const villageObservationSchema = z.object({
  minute: z.number().int().nonnegative(), day: z.number().int().positive(), time: z.string(),
  residents: z.array(z.object({
    id: z.string(), name: z.string(), occupation: z.string(), activity: z.string()
  })),
  stocks: z.record(z.string(), z.number().int().nonnegative()),
  coins: z.number().int().nonnegative(),
  resource_inventory: z.record(z.string(), z.number().int().nonnegative()),
  tasks: z.array(villageTaskSchema.extend({ id: z.string() })),
  events: z.array(villageEventSchema), policy: z.string(),
  notices: z.array(villageEventSchema).optional(),
  wagon: villageWagonSchema.extend({ location: z.string().nullable() }).nullable().optional(),
  personal: z.object({
    occupation: z.string(), personality: z.string(), skills: z.array(z.string()),
    beliefs: z.record(z.string(), villageBeliefSchema),
    relationships: z.record(z.string(), z.number().int()),
    schedule: z.array(villageRoutineSchema), routine: villageRoutineSchema,
    job: villageJobSchema.nullable(), next_work: z.number(), busy_until: z.number(),
    available_at: z.number().int().optional(), known_routes: z.array(z.string()).optional(),
    routes: z.array(z.object({destination: z.string(), path: z.array(z.string()), steps: z.number().int()})).optional(),
    stock_report: z.record(z.string(), z.record(z.string(), z.number().int().nonnegative())).optional(),
    stock_report_at: z.number().int().nullable().optional(), shipments: z.array(villageShipmentSchema).optional(),
    timber_route: z.boolean().optional(), horse_location: z.string().nullable().optional(),
    horse_fed_today: z.boolean().nullable().optional(), deadlines: z.record(z.string(), villageDeadlineSchema).optional(),
    deliveries: z.array(villageDeliverySchema), active_delivery: z.number().int().nullable(),
    life: villageLifeSchema.extend({
      rest_due: z.boolean(), wake_minute: z.number().int(), bed_minute: z.number().int(), home: z.string(), tavern: z.string(),
      cooldown_factor: z.number().int().min(1).max(4).optional()
    }).optional()
  }).optional()
});
export type VillageObservation = z.infer<typeof villageObservationSchema>;
const villageStateSchema = z.object({
  minute: z.number().int().nonnegative(),
  residents: z.record(z.string(), z.object({
    beliefs: z.record(z.string(), villageBeliefSchema),
    memories: z.array(z.number().int().nonnegative()), trust: z.record(z.string(), z.number().int()),
    activity: z.string(), next_work: z.number(), busy_until: z.number(),
    cargo: z.record(z.string(), z.number().int().nonnegative()),
    delivery: z.number().int().nullable(), delivery_cursor: z.number().int(),
    last_news_day: z.number().int().default(-1), life: villageLifeSchema.nullable().default(null),
    available_at: z.number().int().optional(), known_routes: z.array(z.string()).optional(),
    shipments: z.array(villageShipmentSchema).optional(), timber_route: z.boolean().optional(),
    fertilized_harvests: z.number().int().nonnegative().optional()
  })),
  stocks: z.record(z.string(), z.record(z.string(), z.number().int().nonnegative())),
  coins: z.record(z.string(), z.number().int().nonnegative()),
  tasks: z.record(z.string(), villageTaskSchema.extend({
    requester: z.string(), rendezvous: z.string(), progress: z.number().int(),
    evidence: z.array(z.number().int().nonnegative())
  })),
  events: z.array(villageEventSchema), breakdown: z.boolean(), repaired: z.boolean(),
  well_dug: z.boolean(), bread_after_repair: z.number().int().nonnegative(),
  roster: z.record(z.string(), villageResidentSchema).default({}),
  role_contributions: z.record(z.string(), z.number().int().nonnegative()).default({}),
  project_contributions: z.record(z.string(), z.record(z.string(), z.number().int().nonnegative())).default({}),
  shovel_recovered: z.boolean().default(false),
  wagon: villageWagonSchema.nullable().default(null), bread_deliveries: z.number().int().nonnegative().default(0),
  notices: z.array(villageEventSchema).default([]),
  horse_leader: z.string().nullable().optional(), horse_fed_day: z.number().int().optional(),
  deadlines: z.record(z.string(), villageDeadlineSchema).optional(),
  stock_lots: z.record(z.string(), z.record(z.string(), z.array(villageLotSchema))).optional(),
  stock_history: z.array(z.object({
    minute: z.number().int().nonnegative(),
    stocks: z.record(z.string(), z.record(z.string(), z.number().int().nonnegative())),
    coins: z.record(z.string(), z.number().int().nonnegative())
  })).optional()
});
export const observationSchema = z.object({
  contract_version: z.literal("1.0"), world_id: z.string(), session_id: z.string(),
  observation_revision: z.string(),
  village: villageObservationSchema.optional(),
  bbq: z.object({
    target_f: z.number().finite(), tolerance_f: z.number().finite().positive(),
    duration_seconds: z.number().int().positive(), elapsed_seconds: z.number().int().nonnegative(),
    temperature_f: z.number().finite(), heat_percent: z.number().finite().min(0).max(100),
    in_band_seconds: z.number().int().nonnegative(), in_band_percent: z.number().finite().min(0).max(100),
    minimum_in_band_percent: z.number().finite().positive().max(100), in_band: z.boolean(),
    flare_active: z.boolean(), flare_count: z.number().int().nonnegative(),
    max_deviation_f: z.number().finite().nonnegative(), status: z.enum(["tending", "passed", "failed"]),
    history: z.array(z.object({
      seconds: z.number().int().nonnegative(), temperature_f: z.number().finite(),
      heat_percent: z.number().finite().min(0).max(100)
    }))
  }).optional(),
  erp: z.object({
    prompt: z.string(), command: z.string(), output: z.string(),
    invoice_status: z.enum(["on_hold", "matched", "released"])
  }).optional(),
  clock: clockSchema.nullable().optional(),
  bread_batches: z.record(z.string(), breadBatchSchema).optional(),
  ingredient_stock: z.record(z.string(), z.number().int().nonnegative()).optional(),
  voyage: z.object({
    tide: z.enum(["low", "high"]), turns_until_change: z.number().int().positive(),
    policy: z.string(), seaworthy: z.boolean().optional(), crew_aboard: z.boolean().optional()
  }).optional(),
  self: z.object({
    actor_id: z.string(), location_id: z.string(),
    body: z.object({
      id: z.string(), description: z.string(), capabilities: z.array(z.string()),
      ...embodimentFields,
      carrying: z.object({ rule: z.string(), limit: z.number(), used: z.number() })
    }),
    inventory: z.array(z.object({ id: z.string(), name: z.string(), inventory_units: z.number().int().positive().optional() })),
    needs: z.array(needSchema)
  }),
  location: z.object({ id: z.string(), description: z.string() }),
  narrative: z.string(),
  secondary_goals: z.array(z.object({
    id: z.string(), title: z.string(), description: z.string(), status: z.enum(["active", "achieved"]),
    conditions: z.array(z.string())
  })).optional(),
  role: z.string().optional(),
  visible_actors: z.array(z.object({
    actor_id: z.string(), inventory: z.array(z.object({ id: z.string(), name: z.string() })),
    capabilities: z.array(z.string()).optional()
  })).optional(),
  skill_challenges: z.array(z.object({
    id: z.string(), description: z.string(), target: z.string(), capability: z.string(),
    completed_by: z.string().nullable(), reason: z.string().nullable()
  })).optional(),
  messages: z.array(z.object({ actor_id: z.string(), text: z.string() })).optional(),
  opening: z.boolean().optional(),
  discovered_goal: z.object({ objective: z.string().min(1), type: z.string().min(1), source_id: z.string().min(1) }).optional(),
  visible_entities: z.array(z.object({
    id: z.string(), description: z.string(), food: z.boolean().optional(),
    nutrition: nutritionSchema.nullable().optional(),
    carry_grips: gripsSchema.optional(), use_grips: gripsSchema.optional(),
    preparation: z.record(z.string(), z.string()).optional(),
    ingredients: z.array(z.string()).optional(),
    power: z.enum(["off", "on"]).optional(), plugged_into: z.string().nullable().optional(),
    text: z.string().optional(),
    traversal_capabilities: z.array(z.string()).optional(),
    max_body_size: bodySizeSchema.optional(),
    required_work: z.array(z.string()).optional()
  }).passthrough()),
  actions: z.array(z.object({
    id: z.string(), description: z.string(), arguments_schema: record,
    availability: z.enum(["available", "unknown", "blocked"]),
    reason: z.string().nullable().optional(),
    target_requirements: z.record(z.string(), z.string().nullable()).optional()
  }).passthrough())
});
export const configSchema = z.object({
  schema_version: z.literal("1.0"),
  proxy: z.object({
    base_url: z.string(), model: z.string().nullable(), token_environment: z.string().nullable(),
    timeout_seconds: z.number()
  }),
  world: z.object({
    transport: z.enum(["stdio", "streamable_http"]), scenario: z.string(),
    command: z.string().nullable(), args: z.array(z.string()), url: z.string().nullable()
  }),
  budgets: z.object({
    max_actions: z.number(), max_model_calls: z.number(), max_seconds: z.number(), repetition_window: z.number()
  })
});
export const worldEditProposalSchema = z.object({
  id: z.string(), revision: z.string(), instruction: z.string(), status: z.enum(["pending", "applied"]),
  plan: z.object({
    summary: z.string(),
    additions: z.record(z.string(), z.object({
      name: z.string(), kind: z.string(), description: z.string(),
      location: z.string().nullable().optional(), destination: z.string().nullable().optional(),
      hidden: z.boolean().optional(), discovery_target: z.string().nullable().optional()
    }).passthrough()),
    key_bindings: z.record(z.string(), z.string()),
    secondary_goals: z.record(z.string(), secondaryGoalSpecSchema).default({})
  })
});
export const npcChatSchema = z.object({ question: z.string(), answer: z.string(), private: z.literal(true) });
export const budgetExtensionSchema = z.strictObject({
  run_id: z.string().min(1),
  add_actions: z.number().int().nonnegative(),
  add_model_calls: z.number().int().nonnegative(),
  add_seconds: z.number().nonnegative()
}).refine(value => value.add_actions > 0 || value.add_model_calls > 0 || value.add_seconds > 0,
  "Add at least one action, model call, or minute.");
export const runSchema = z.object({
  schema_version: z.literal("1.0"), id: z.string(), world_id: z.string(),
  session_id: z.string(), actor_id: z.string(), title: z.string(), scenario_id: z.string(),
  status: z.string(), reason: z.string(), created_at: z.string(),
  actions: z.number(), model_calls: z.number(), cycles: z.number(), elapsed_seconds: z.number(),
  followup_model_calls: z.number().int().nonnegative().optional(),
  previous_review_model_calls: z.number().int().nonnegative().optional(),
  config: configSchema.optional(),
  model: z.string().nullable(),
  successful: z.boolean().nullable().optional(),
  completion_analysis: completionAnalysisSchema.optional(),
  agent_assignments: z.record(z.string(), z.string()).optional(),
  world_edit_proposals: z.record(z.string(), worldEditProposalSchema).optional(),
  skill_mode: skillModeSchema.optional(),
  dungeon_master: z.object({
    config: dungeonMasterConfigSchema, model_calls: z.number().int().nonnegative(),
    last_review_action: z.number().int().nonnegative()
  }).optional(),
  agent_profiles: z.record(z.string(), agentProfileSchema).optional(),
  agents: z.record(z.string(), z.object({
    role: z.string(), memory: record, observation: observationSchema.nullable()
  }).passthrough()).optional(),
  goal: z.object({ id: z.string(), objective: z.string(), type: z.string(), status: z.string(),
    revealed: z.boolean().optional() })
}).passthrough();
export const worldSchema = z.object({
  village: villageStateSchema.nullable().optional(),
  secondary_goals: z.record(z.string(), secondaryGoalSchema).default({}),
  world_id: z.string(), revision: z.number(),
  clock: clockSchema.nullable().optional(),
  batches: z.record(z.string(), breadBatchSchema).optional(),
  ingredient_stock: z.record(z.string(), z.number().int().nonnegative()).optional(),
  exit_location: z.string().nullable().optional(),
  entities: z.record(z.string(), z.object({
    name: z.string(), kind: z.string(), description: z.string(),
    location: z.string().nullable().optional(), open: z.boolean().nullable().optional(),
    traversal_capabilities: z.array(z.string()).optional(),
    max_body_size: bodySizeSchema.nullable().optional(),
    required_challenges: z.array(z.string()).optional(),
    locked: z.boolean().nullable().optional(), takable: z.boolean().optional(),
    inventory_units: z.number().int().positive().optional(),
    carry_grips: gripsSchema.optional(), use_grips: gripsSchema.optional(),
    food: z.boolean().optional(), nutrition: nutritionSchema.nullable().optional(),
    readable_text: z.string().nullable().optional(), preparation: z.record(z.string(), z.string()).optional(),
    ingredients: z.array(z.string()).optional(),
    power: z.enum(["off", "on"]).nullable().optional(), plugged_into: z.string().nullable().optional(),
    damage: z.number().optional(), health: z.number().nullable().optional(), max_health: z.number().nullable().optional(),
    destination: z.string().nullable().optional(), lock_id: z.string().nullable().optional(), jammed: z.boolean().optional()
  }).passthrough()),
  actors: z.record(z.string(), z.object({
    actor_id: z.string(), location_id: z.string(), start_location_id: z.string(), inventory: z.array(z.string()),
    visited_locations: z.array(z.string()).optional(),
    inspected: z.array(z.string()).optional(), examined: z.array(z.string()).optional(),
    read_entities: z.array(z.string()).optional(),
    body: z.object({ description: z.string(), capabilities: z.array(z.string()), capacity: z.number(), ...embodimentFields }),
    needs: z.record(z.string(), z.object({
      enabled: z.boolean(), level: z.number(), rate: z.number(), threshold: z.number()
    }))
  }).passthrough())
}).passthrough();
export const stateSchema = z.object({
  run: runSchema.nullable(), observation: observationSchema.nullable(),
  world: worldSchema.nullable(), memory: record,
  in_flight: z.boolean(), event_count: z.number(), config: configSchema
});
export const eventSchema = z.object({
  id: z.string(), sequence: z.number(), run_id: z.string(), session_id: z.string(),
  actor_id: z.string(), kind: z.string(), cycle: z.number(), timestamp: z.string(),
  village_minute: z.number().int().nonnegative().optional(), actor_name: z.string().optional(),
  free_action: z.boolean().optional(),
  payload: z.unknown()
});
export const eventsSchema = z.object({ run_id: z.string().nullable(), events: z.array(eventSchema) });
export const modelsSchema = z.object({ models: z.array(z.string()) });
export const scenariosSchema = z.object({ scenarios: z.array(z.object({
  id: z.string(), title: z.string(), objective: z.string(),
  agent_slots: z.array(z.object({ id: z.string(), profile_id: z.string(), start_location: z.string() }))
})) });
export const historySchema = z.object({ runs: z.array(runSchema) });
export const historyDetailSchema = z.object({ run: runSchema, events: z.array(eventSchema) });
export type State = z.infer<typeof stateSchema>;
export type World = z.infer<typeof worldSchema>;
export type RunEvent = z.infer<typeof eventSchema>;
