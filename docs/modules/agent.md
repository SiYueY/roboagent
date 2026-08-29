# Agent Module

`agent.loop` is a stateless sequential execution loop. `Agent` owns the
transcript, run lock, cancellation control, and observer subscriptions. Context
transforms operate on immutable `ModelContext`; before/after tool hooks are the
only execution-policy extension points.
