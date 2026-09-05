# Skill Module

Skills are bounded guidance, not executors, permissions, routers, or policy.
`SkillLoader` scans only direct child directories of project and user roots,
parses strict `SKILL.md` frontmatter, and produces deterministic diagnostics.

`SkillManager.reload()` creates a new immutable `SkillCatalog` revision for
future Runs. An active Run pins one revision for both prompt metadata and
`read_skill` content. `create_read_skill_tool()` returns an ordinary explicit
`READ_ONLY`, `CONCURRENT` Tool; it is never registered automatically.
