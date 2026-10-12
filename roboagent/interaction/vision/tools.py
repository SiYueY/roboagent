"""Explicit observation Tools; capture and application authorization remain Host-owned."""

from dataclasses import replace
import asyncio
from roboagent.runtime.execution import RuntimeToolExecutionContext
from uuid import uuid4

from roboagent.message import freeze_json, freeze_json_object
from roboagent.runtime import UsageContribution, UsageKnowledge
from roboagent.tool import (
    Tool,
    ToolDefinition,
    ToolEffectKind,
    ToolErrorInfo,
    ToolExecutionFailure,
    ToolJsonContent,
    ToolEffectRecord,
    ToolEffectStatus,
    ToolEffectReporting,
    CompositeToolOutcome,
    CompositeToolExecutionFailure,
)
from .image import to_data
from .geometry import solve_pnp, transform_pose
from .types import (
    EstimateStatus,
    SpatialCapture,
    SpatialResults,
    VisionError,
    VisualQuery,
)


def create_vision_tools(
    capture,
    semantic,
    *,
    spatial=None,
    tracker=None,
    world_adapter=None,
    correspondences=None,
    camera=None,
    transforms=None,
    target_frame=None,
    resolve_entity=None,
):
    """Compose one synchronous observation boundary without another Agent/Runtime.

    capture(target) returns immutable VisualCapture or SpatialCapture. Host capture
    backend confirms and activates the tracker epoch before any cloud await; this
    Tool never activates epochs from returned captures. resolve_entity validates a targeted
    identity; it never assigns Model-produced writer identity.
    """

    async def observe(arguments, context, *, tool_name):
        cancellation = context.cancellation
        effects = []
        execution = (
            context.execution
            if isinstance(context.execution, RuntimeToolExecutionContext)
            else None
        )
        call_id = (
            execution.lineage.tool_call_id
            if execution and execution.lineage.tool_call_id
            else "direct:" + uuid4().hex
        )

        def receipt_effect(stage, receipt):
            content = ToolJsonContent(
                freeze_json({"stage": stage, "receipt": to_data(receipt)})
            )
            effect = ToolEffectRecord(
                call_id,
                tool_name,
                ToolEffectKind.SIDE_EFFECTING,
                ToolEffectStatus.SUCCEEDED,
                content=content,
                effect_id=execution.next_effect_id() if execution else None,
            )
            effects.append(effect)
            # Journal the actual commit immediately. Cancellation can happen
            # before the helper returns its composite outcome.
            if execution:
                execution.contribute_effects((effect,))

        def fail(error):
            if world_adapter is None:
                raise ToolExecutionFailure(error)
            failed = ToolEffectRecord(
                call_id,
                tool_name,
                ToolEffectKind.SIDE_EFFECTING,
                ToolEffectStatus.FAILED,
                error=error,
                effect_id=execution.next_effect_id() if execution else None,
            )
            raise CompositeToolExecutionFailure(error, effects=(*effects, failed))

        try:
            cancellation.raise_if_cancelled()
            target = arguments.get("entity_id")
            if target is not None and resolve_entity is None:
                raise VisionError("entity_resolution_unavailable")
            query = (
                await resolve_entity(target)
                if target is not None
                else VisualQuery(arguments.get("query"))
            )
            if not isinstance(query, VisualQuery):
                raise VisionError("entity_resolution_unavailable")
            sample = await capture(target)
            visual = sample.visual if isinstance(sample, SpatialCapture) else sample
            cancellation.raise_if_cancelled()

            def usage(value):
                if context.execution is not None:
                    context.execution.contribute_usage(
                        UsageContribution(
                            UsageKnowledge.KNOWN
                            if value is not None
                            else UsageKnowledge.UNKNOWN,
                            value,
                        )
                    )

            analysis = await semantic.analyze(
                visual,
                query,
                analysis_id=uuid4().hex,
                cancellation=cancellation,
                on_usage=usage,
            )
            cancellation.raise_if_cancelled()
            estimates = (
                spatial.analyze(sample, analysis.scene)
                if spatial and isinstance(sample, SpatialCapture)
                else SpatialResults(sample.ref)
            )
            poses = []
            if correspondences:
                if not isinstance(sample, SpatialCapture) and camera is None:
                    raise VisionError("camera_calibration_unavailable")
                selected_camera = (
                    sample.visual_camera
                    if isinstance(sample, SpatialCapture)
                    else await camera(visual)
                )
                if (selected_camera.width, selected_camera.height) != (
                    visual.image.width,
                    visual.image.height,
                ):
                    raise VisionError("camera_dimension_mismatch")
                inputs = await correspondences(visual, analysis.scene)
                for item in inputs:
                    if item.capture_ref != sample.ref or item.detection_id not in {
                        d.local_id for d in analysis.scene.objects
                    }:
                        raise VisionError("estimate_binding_invalid")
                    poses.append(solve_pnp(item, selected_camera))
            estimates = replace(estimates, poses=tuple(poses))
            if target_frame is not None:
                if transforms is None:
                    raise VisionError("transform_unavailable")

                async def move(result):
                    cancellation.raise_if_cancelled()
                    if (
                        result.estimate is None
                        or result.estimate.frame_id == target_frame
                    ):
                        return result
                    point = await transforms.transform_point(
                        result.estimate.xyz,
                        source_frame=result.estimate.frame_id,
                        target_frame=target_frame,
                        captured_at=visual.captured_at,
                    )
                    if point is None:
                        return type(result)(
                            result.capture_ref,
                            result.detection_id,
                            EstimateStatus.UNAVAILABLE,
                            None,
                            "transform_unavailable",
                        )
                    return type(result)(
                        result.capture_ref,
                        result.detection_id,
                        EstimateStatus.AVAILABLE,
                        replace(result.estimate, xyz=point, frame_id=target_frame),
                    )

                surface = [await move(result) for result in estimates.surface]
                positions = [await move(result) for result in estimates.positions]
                transformed_poses = []
                for result in estimates.poses:
                    transformed_poses.append(
                        await transform_pose(
                            result,
                            transforms,
                            target_frame=target_frame,
                            captured_at=visual.captured_at,
                        )
                    )
                # Principal axes retain their explicit source frame, rather than
                # being mislabeled or fabricated into an object pose for TF.
                estimates = replace(
                    estimates,
                    surface=tuple(surface),
                    positions=tuple(positions),
                    poses=tuple(transformed_poses),
                )
            cancellation.raise_if_cancelled()
            tracking = tracker.update(sample, analysis, estimates) if tracker else None
            publication = None
            if world_adapter:
                if tracking is None:
                    raise VisionError("tracking_required")
                prepared = world_adapter.prepare(sample, analysis, tracking, estimates)
                cancellation.raise_if_cancelled()
                publication = await world_adapter.publish(
                    prepared, cancellation=cancellation, on_receipt=receipt_effect
                )
                if publication.error_code:
                    revision = (
                        publication.evidence_receipt.revision
                        if publication.evidence_receipt
                        else None
                    )
                    fail(
                        ToolErrorInfo(
                            publication.error_code,
                            f"Vision publication failed: stage={publication.status.value}; evidence_revision={revision}.",
                        )
                    )
            content = ToolJsonContent(
                freeze_json(
                    {
                        "capture_ref": to_data(sample.ref),
                        "analysis_id": analysis.analysis_id,
                        "semantic_status": "available",
                        "scene": to_data(analysis.scene),
                        "spatial": to_data(estimates),
                        "tracking": to_data(tracking),
                        "publication": to_data(publication),
                    }
                )
            )
            return (
                CompositeToolOutcome((content,), tuple(effects))
                if world_adapter
                else content
            )
        except VisionError as exc:
            fail(ToolErrorInfo(exc.code, exc.code))
        except asyncio.CancelledError:
            # Known commits are already in canonical execution state.
            raise

    tools = []
    for name in ("observe_scene", "observe_entity"):
        properties = (
            {"query": {"type": "string", "maxLength": 4096}}
            if name == "observe_scene"
            else {
                "entity_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "x-world-entity-ref": True,
                }
            }
        )
        definition = ToolDefinition(
            name,
            "Acquire explicit visual evidence and return bounded semantic/spatial results.",
            freeze_json_object(
                {
                    "type": "object",
                    "properties": properties,
                    "required": [] if name == "observe_scene" else ["entity_id"],
                    "additionalProperties": False,
                }
            ),
        )

        async def invoke(arguments, context, tool_name=name):
            return await observe(arguments, context, tool_name=tool_name)

        tools.append(
            Tool(
                definition,
                invoke,
                effect_kind=ToolEffectKind.SIDE_EFFECTING
                if world_adapter
                else ToolEffectKind.READ_ONLY,
                effect_reporting=ToolEffectReporting.COMPOSITE
                if world_adapter
                else ToolEffectReporting.LEAF,
            )
        )
    return tuple(tools)
