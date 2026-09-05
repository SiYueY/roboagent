# Context Module

`ContextManager.prepare(request, cancellation)` projects an immutable
`ContextRequest` into a provider-neutral `PreparedContext`. It computes only;
`Session` exclusively owns compaction mutation and persistence.

`FullContextManager` preserves the full transcript. `WindowContextManager`
removes only complete message groups, so an assistant tool-call message and all
of its ordered results are never split.

System prompts are rendered in fixed order: base `PromptInput`, runtime
instructions, then normalized and sorted Skill metadata. Skill paths and bodies
are not rendered.

`CompactingContextManager` budgets the complete request, including prompt,
skills, tool schemas, provider framing, summaries, artifacts, and multimodal
content. Compaction preserves complete ToolExchange groups and at least one
recent user turn. Incremental compaction sends an existing summary plus only
new canonical groups to the summarizer; its source digest still covers the
original canonical transcript prefix.

`SummarySegment` is projected below system authority. Successful and failed
compaction observations use `context.compaction_completed` and
`context.compaction_failed`; event payloads contain metadata, never summary
text.
