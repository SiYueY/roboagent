# Config Module

`AppConfig` loads and validates provider model entries, bounded Skill settings,
and optional speech settings. YAML values support strict
`${VARIABLE}` expansion using the process environment and a sibling `.env`.

`to_model_registry()` creates the configuration registry used by
`create_model()` or `ConfiguredModelProvider`. `create_skill_manager()` creates
the guidance-only `SkillManager`; Skill configuration contains size bounds, not
permissions, routing, entrypoints, or executable schemas.
