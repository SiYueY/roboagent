# Context Module

`ContextManager.prepare(snapshot, cancellation)` projects an immutable
`ContextSnapshot` into a provider-neutral `ModelContext`.

`FullContextManager` preserves the full transcript. `WindowContextManager`
removes only complete message groups, so an assistant tool-call message and all
of its ordered results are never split.

System prompts are rendered in fixed order: base `PromptInput`, runtime
instructions, then normalized and sorted Skill metadata. Skill paths and bodies
are not rendered.
