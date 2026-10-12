"""Explicit robot application mapping over first-party visual primitives.

Device capture, calibration, stable robot object identities and preflight remain
Host inputs. This module contains no geometry solver or separate perception state.
"""

from roboagent.interaction.vision import (
    SemanticAnalyzer,
    SpatialAnalyzer,
    VisualTracker,
    VisionWorldAdapter,
    VisionWorldBinding,
    create_vision_tools,
)


def create_perception_tools(
    *,
    capture,
    model,
    world,
    stream_id,
    namespace,
    observation_source,
    semantic_source,
    geometry_source,
    track_to_entity,
    transforms,
    target_frame,
    correspondences=None,
    camera=None,
    resolve_entity=None,
    scope="workspace",
):
    """Register an explicit example composition; no sensor automatically starts a Run.

    Full object pose is produced only by validated PnP correspondences. A robot
    deployment must bind tracker identity to its actual controller object IDs.
    Semantic spatial relations retain namespaced predicates, never task success.
    """

    def encode(predicate, value):
        if predicate == "vision.geometry.pose":
            return {
                "xyz": value["position"],
                "quaternion": value["orientation_wxyz"],
                "frame_id": value["frame_id"],
                "object_frame_id": value["object_frame_id"],
                "method": value["method"],
            }
        return value

    binding = VisionWorldBinding(
        world,
        scope,
        observation_source,
        semantic_source,
        geometry_source,
        track_to_entity=track_to_entity,
        predicate_mapping={
            "vision.semantic.category": "category",
            "vision.semantic.color": "color",
            "vision.geometry.pose": "pose",
        },
        value_encoder=encode,
    )
    return create_vision_tools(
        capture,
        SemanticAnalyzer(model),
        spatial=SpatialAnalyzer(),
        tracker=VisualTracker(namespace=namespace, stream_id=stream_id),
        world_adapter=VisionWorldAdapter(binding),
        transforms=transforms,
        target_frame=target_frame,
        correspondences=correspondences,
        camera=camera,
        resolve_entity=resolve_entity,
    )
