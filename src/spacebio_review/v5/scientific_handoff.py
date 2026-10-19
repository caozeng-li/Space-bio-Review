"""Originals accompanying scientific work come from reading, not citation parsing."""

import json

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.prose import prose_inputs


def manuscript_tokens(view):
    """Price a manuscript view as displayed prose, excluding its separate originals."""
    return TokenEstimator().measure_texts([
        json.dumps(prose_inputs(view), ensure_ascii=False),
    ])["input_tokens"]


def delivery_readings(access, *, artifact=None, additional=(), source_id=None):
    """Collect actual captured originals, retaining an exact draft's cited old versions.

    This is the cumulative archive available to a recipient. Its active conversation
    decides which complete units need displaying; downloading a source never adds its
    unread passages here.
    """
    readings = access.references.snapshot_choices(selected=False)
    for captured in additional:
        readings.update(captured)
    if artifact:
        readings.update(artifact["evidence"])
    if source_id is not None:
        readings = {handle: snapshot for handle, snapshot in readings.items()
                    if snapshot["reference"]["source_id"] == source_id}
    return readings
