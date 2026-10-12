"""Host-owned bounded association. No World, Session or Runtime state lives here."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import math

from roboagent.message import canonical_json_digest, canonical_json_dumps
from .image import capture_digest, to_data
from .types import (
    PositionEstimate,
    SemanticAnalysis,
    SpatialCapture,
    VisualCapture,
    VisualDetection,
)
from .types import (
    CaptureRef,
    EstimateStatus,
    SpatialResults,
    VisionError,
    VisionLimits,
    finite,
    text_id,
)


@dataclass(frozen=True, slots=True, order=True)
class TrackId:
    namespace: str
    clock_id: str
    value: str

    def __post_init__(self):
        for value in (self.namespace, self.clock_id, self.value):
            text_id(value)


class AssociationStatus(str, Enum):
    MATCHED = "matched"
    NEW = "new"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class Association:
    detection_id: str
    status: AssociationStatus
    track_id: TrackId | None
    candidate_tracks: tuple[TrackId, ...] = ()

    def __post_init__(self):
        text_id(self.detection_id)
        candidates = tuple(sorted(set(self.candidate_tracks)))
        object.__setattr__(self, "candidate_tracks", candidates)
        if not isinstance(self.status, AssociationStatus):
            raise VisionError("association_invalid")
        if self.status is AssociationStatus.AMBIGUOUS:
            if self.track_id is not None or not candidates:
                raise VisionError("association_invalid")
        elif not isinstance(self.track_id, TrackId) or candidates:
            raise VisionError("association_invalid")


class TrackingStatus(str, Enum):
    UPDATED = "updated"
    CACHED = "cached"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TrackingResult:
    capture_ref: CaptureRef
    status: TrackingStatus
    authoritative_analysis_id: str | None
    matching_input_digest: str | None
    associations: tuple[Association, ...]
    reason: str | None = None
    input_digest: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "associations", tuple(self.associations))
        if self.status is TrackingStatus.SKIPPED:
            if self.associations or not self.reason:
                raise VisionError("association_invalid")
        elif (
            self.reason
            or not self.authoritative_analysis_id
            or not self.matching_input_digest
        ):
            raise VisionError("association_invalid")
        ids = [a.detection_id for a in self.associations]
        assigned = [a.track_id for a in self.associations if a.track_id is not None]
        if any(t.clock_id != self.capture_ref.clock_id for t in assigned):
            raise VisionError("association_invalid")
        if len(set(ids)) != len(ids) or len(set(assigned)) != len(assigned):
            raise VisionError("association_invalid")


@dataclass(frozen=True, slots=True)
class TrackingPolicy:
    min_iou: float = 0.05
    max_anchor_distance: float = 0.25
    ambiguity_margin: float = 0.05
    max_position_distance: float = 0.2
    stable_attributes: tuple[str, ...] = ("color",)
    stable_frames: frozenset[str] = frozenset()

    def __post_init__(self):
        finite(
            self.min_iou,
            self.max_anchor_distance,
            self.ambiguity_margin,
            self.max_position_distance,
        )
        if (
            not 0 <= self.min_iou <= 1
            or min(
                self.max_anchor_distance,
                self.ambiguity_margin,
                self.max_position_distance,
            )
            < 0
        ):
            raise VisionError("tracking_policy_invalid")
        object.__setattr__(self, "stable_attributes", tuple(self.stable_attributes))
        object.__setattr__(self, "stable_frames", frozenset(self.stable_frames))


@dataclass(frozen=True, slots=True)
class _Track:
    detection: VisualDetection
    timestamp: int
    missed: int = 0
    position: PositionEstimate | None = None


def iou(a, b):
    intersection = max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(
        0, min(a.y1, b.y1) - max(a.y0, b.y0)
    )
    union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - intersection
    return intersection / union


def association_input_digest(capture, scene, spatial):
    if (
        capture.ref != scene.ref
        or spatial is not None
        and spatial.capture_ref != capture.ref
    ):
        raise VisionError("estimate_binding_invalid")
    positions = (
        sorted(
            (r for r in spatial.positions if r.status is EstimateStatus.AVAILABLE),
            key=lambda r: r.detection_id,
        )
        if spatial
        else []
    )
    objects = [to_data(d) for d in sorted(scene.objects, key=lambda d: d.local_id)]
    for obj in objects:
        obj.pop("description", None)
    return canonical_json_digest(
        {
            "capture": capture_digest(capture),
            "objects": objects,
            "positions": to_data(positions),
        }
    )


def matching_digest(capture, scene, spatial, policy):
    return canonical_json_digest(
        [
            association_input_digest(capture, scene, spatial),
            {**to_data(policy), "stable_frames": sorted(policy.stable_frames)},
        ]
    )


class VisualTracker:
    def __init__(
        self,
        *,
        namespace: str,
        stream_id: str,
        limits: VisionLimits | None = None,
        policy: TrackingPolicy | None = None,
    ):
        text_id(namespace)
        text_id(stream_id)
        self.namespace, self.stream_id = namespace, stream_id
        self.limits, self.policy = limits or VisionLimits(), policy or TrackingPolicy()
        self.clock_id: str | None = None
        self._last_sequence = -1
        self._last_time = -1
        self._counter = 0
        self._tracks: dict[TrackId, _Track] = {}
        self._cache: OrderedDict[CaptureRef, tuple[str, TrackingResult, int]] = (
            OrderedDict()
        )

    def activate_epoch(self, clock_id: str) -> None:
        text_id(clock_id)
        if clock_id != self.clock_id:
            self.clock_id = clock_id
            self._last_sequence = self._last_time = -1
            self._counter = 0
            self._tracks.clear()
            self._cache.clear()

    def _cost(
        self,
        track: _Track,
        detection: VisualDetection,
        position: PositionEstimate | None,
    ) -> float | None:
        previous = track.detection
        if previous.category.casefold() != detection.category.casefold():
            return None
        for key in self.policy.stable_attributes:
            if (
                key in previous.attributes
                and key in detection.attributes
                and previous.attributes[key] != detection.attributes[key]
            ):
                return None
        overlap = iou(previous.region, detection.region)
        distance = math.hypot(
            previous.anchor.x - detection.anchor.x,
            previous.anchor.y - detection.anchor.y,
        )
        cost = (1 - overlap + distance) / 2
        if (
            position is not None
            and track.position is not None
            and position.frame_id == track.position.frame_id
            and position.frame_id in self.policy.stable_frames
        ):
            metric = math.dist(position.xyz, track.position.xyz)
            if metric > self.policy.max_position_distance:
                return None
            # Stable metric evidence survives camera motion. Image agreement is
            # only a secondary cost and cannot veto this candidate.
            return metric / max(self.policy.max_position_distance, 1e-12) + 0.05 * cost
        if overlap < self.policy.min_iou or distance > self.policy.max_anchor_distance:
            return None
        return cost

    def update(
        self,
        capture: VisualCapture | SpatialCapture,
        analysis: SemanticAnalysis,
        spatial: SpatialResults | None = None,
    ) -> TrackingResult:
        scene = analysis.scene
        ref = capture.ref

        def skipped(reason):
            return TrackingResult(ref, TrackingStatus.SKIPPED, None, None, (), reason)

        if ref.stream_id != self.stream_id:
            return skipped("tracking_stream_mismatch")
        if ref.clock_id != self.clock_id:
            return skipped("tracking_epoch_inactive")
        scene.validate_limits(self.limits)
        digest = matching_digest(capture, scene, spatial, self.policy)
        media_digest = capture_digest(capture)
        cached = self._cache.get(ref)
        if cached:
            old_media, result, _ = cached
            if old_media != media_digest:
                return skipped("capture_content_conflict")
            if result.matching_input_digest != digest:
                return skipped("tracking_analysis_conflict")
            return TrackingResult(
                ref,
                TrackingStatus.CACHED,
                result.authoritative_analysis_id,
                digest,
                result.associations,
                input_digest=result.input_digest,
            )
        if any(key.sequence == ref.sequence for key in self._cache):
            return skipped("tracking_sequence_conflict")
        if ref.sequence <= self._last_sequence:
            return skipped(
                "tracking_retry_expired"
                if ref.sequence == self._last_sequence
                else "tracking_out_of_order"
            )
        visual = capture.visual if hasattr(capture, "visual") else capture
        now = visual.captured_at.nanoseconds
        if now < self._last_time:
            return skipped("tracking_time_regression")
        active = {
            tid: t
            for tid, t in self._tracks.items()
            if now - t.timestamp <= self.limits.max_track_age_ns
            and t.missed < self.limits.max_missed_observations
        }
        positions = (
            {
                r.detection_id: r.estimate
                for r in spatial.positions
                if r.estimate is not None
            }
            if spatial
            else {}
        )
        candidates: dict[str, list[tuple[float, TrackId]]] = {
            d.local_id: [] for d in scene.objects
        }
        for detection in scene.objects:
            for tid, track in active.items():
                cost = self._cost(track, detection, positions.get(detection.local_id))
                if cost is not None:
                    candidates[detection.local_id].append((cost, tid))
            candidates[detection.local_id].sort()
        ambiguous = set()
        for det, choices in candidates.items():
            if (
                len(choices) > 1
                and choices[1][0] - choices[0][0] <= self.policy.ambiguity_margin
            ):
                ambiguous.add(det)
        for tid in active:
            competition = sorted(
                (cost, det)
                for det, choices in candidates.items()
                for cost, t in choices
                if t == tid
            )
            if (
                len(competition) > 1
                and competition[1][0] - competition[0][0]
                <= self.policy.ambiguity_margin
            ):
                ambiguous.update(
                    det
                    for cost, det in competition
                    if cost - competition[0][0] <= self.policy.ambiguity_margin
                )
        assigned = {}
        occupied = set()
        for cost, tid, det in sorted(
            (cost, tid, det)
            for det, choices in candidates.items()
            for cost, tid in choices
        ):
            if det not in ambiguous and det not in assigned and tid not in occupied:
                assigned[det] = tid
                occupied.add(tid)
        associations = []
        counter = self._counter
        new_tracks = {
            tid: _Track(t.detection, t.timestamp, t.missed + 1, t.position)
            for tid, t in active.items()
        }
        for detection in sorted(scene.objects, key=lambda d: d.local_id):
            det = detection.local_id
            choices = candidates[det]
            if det in ambiguous or det not in assigned and choices:
                associations.append(
                    Association(
                        det,
                        AssociationStatus.AMBIGUOUS,
                        None,
                        tuple(tid for _, tid in choices),
                    )
                )
                continue
            if det in assigned:
                tid = assigned[det]
                status = AssociationStatus.MATCHED
            else:
                counter += 1
                tid = TrackId(self.namespace, ref.clock_id, str(counter))
                status = AssociationStatus.NEW
            associations.append(Association(det, status, tid))
            new_tracks[tid] = _Track(detection, now, position=positions.get(det))
        result = TrackingResult(
            ref,
            TrackingStatus.UPDATED,
            analysis.analysis_id,
            digest,
            tuple(associations),
            input_digest=association_input_digest(capture, scene, spatial),
        )
        size = len(
            canonical_json_dumps(
                {"capture": media_digest, "result": to_data(result)}
            ).encode()
        )
        if (
            len(new_tracks) > self.limits.max_tracks
            or size > self.limits.max_tracking_cache_bytes
        ):
            return skipped("tracking_capacity_exceeded")
        self._tracks = new_tracks
        self._counter = counter
        self._last_sequence = ref.sequence
        self._last_time = now
        self._cache[ref] = (media_digest, result, size)
        while (
            len(self._cache) > self.limits.max_tracking_cache_frames
            or sum(entry[2] for entry in self._cache.values())
            > self.limits.max_tracking_cache_bytes
        ):
            self._cache.popitem(last=False)
        return result
