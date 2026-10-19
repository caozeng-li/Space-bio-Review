"""One author's active, in-memory dialogue and the originals actually read in it."""

from __future__ import annotations

import json
from copy import deepcopy

from spacebio_review.agents.context import TokenEstimator
from spacebio_review.agents.reading import ReadingWindow
from spacebio_review.evidence.reading_render import render_originals

from .research_context import expand_captured_units


def _unit(snapshot):
    reference = snapshot.get("reference", {})
    return (
        reference.get("source_id"),
        snapshot.get("material", {}).get("material_id"),
        snapshot.get("reading_structure", {}).get("unit_id")
        or reference.get("passage_id") or reference.get("visual_id"),
    )


def _position(snapshot):
    reference = snapshot.get("reference", {})
    return (
        *_unit(snapshot), reference.get("passage_id"), reference.get("visual_id"),
        snapshot.get("material_version"),
    )


class ActiveConversation:
    """Keep protocol history and a bounded-viewable reading set, without persistence.

    The runtime supplies actual public events and controls their observation watermark.
    A manuscript's compiled citations never decide whether an original was read or should
    remain available. Native units retain their exact positioned versions.
    """

    def __init__(self, messages=(), *, observation_index=0):
        self.messages = list(messages)
        self.observation_index = observation_index
        self.reading_window = ReadingWindow()
        self.pending_read = None
        self.schemas = []
        self.shown_handles: set[str] = set()
        # Actual source-author inputs survive rebase; resident originals may not.
        self.presented_handles: set[str] = set()
        self.readings: dict[str, dict] = {}
        self._visual_urls: dict[str, str] = {}
        self.prepared_page_handles: set[str] = set()
        self.reading_index = 0
        self._positions: dict[tuple, str] = {}
        self._unit_read_at: dict[tuple, int] = {}

    def append(self, message):
        # Provider-required fields stay in this live request chain as returned.
        self.messages.append(message)

    def ack(self, observation_index):
        self.observation_index = max(self.observation_index, observation_index)

    def remember(self, readings, *, reread=False):
        """Remember delivered originals; an explicit reread renews native-unit recency.

        Repeated directory/evidence views do not count as reading again. Callers mark
        returned identities shown only after placing their originals in the dialogue.
        """
        touched = set()
        for alias, snapshot in readings.items():
            position = _position(snapshot)
            if position not in self._positions:
                self._positions[position] = alias
                self.readings[alias] = deepcopy(snapshot)
                touched.add(_unit(snapshot))
            elif reread:
                touched.add(_unit(snapshot))
        if touched:
            self.reading_index += 1
            for unit in touched:
                self._unit_read_at[unit] = self.reading_index
        return self.unshown_readings(readings)

    def unshown_readings(self, readings=None):
        """Return exact originals not already readable in this active history."""
        handles = (
            dict.fromkeys(self._positions[_position(item)] for item in readings.values())
            if readings is not None else self.readings
        )
        return {
            alias: self.readings[alias] for alias in handles
            if alias not in self.shown_handles
        }

    def readings_since(self, reading_index=0):
        """Actual newly read or reread native units since an existing read watermark."""
        return {
            alias: deepcopy(snapshot) for alias, snapshot in self.readings.items()
            if self._unit_read_at[_unit(snapshot)] > reading_index
        }

    def remember_visual(self, readings, source_id, visual_id, image_digest, url):
        """Keep this actually opened page beside its existing positioned alias."""
        selected = self.cache_visual(readings, source_id, visual_id, image_digest, url)
        self.remember(selected, reread=True)
        return selected

    def cache_visual(self, readings, source_id, visual_id, image_digest, url):
        """Cache incoming pixels; selection, not loading, decides what the author sees."""
        selected = {
            alias: item for alias, item in readings.items()
            if item.get("reference", {}).get("source_id") == source_id
            and item.get("reference", {}).get("visual_id") == visual_id
            and item.get("image_digest") == image_digest
        }
        for alias, item in selected.items():
            self._visual_urls[self._positions.get(_position(item), alias)] = url
        return selected

    def visual_messages(self, originals):
        """Project selected original pages once, identically for display and pricing.

        A vision author can open pages of received originals as well as direct tool
        reads. References without cached pixels remain navigation.
        """
        content, seen = [], set()
        for source in originals:
            for material in source.get("materials", []):
                for unit in material.get("units", []):
                    for passage in unit.get("passages", []):
                        alias = passage.get("evidence_handle")
                        if alias not in self._visual_urls or alias in seen:
                            continue
                        seen.add(alias)
                        content.extend([
                            {"type": "text", "text": json.dumps({
                                "source_id": source["source_id"],
                                "material_id": material.get("material_id"),
                                "visual_id": passage["visual_id"],
                                "locator": passage.get("locator"),
                                "citation": passage.get("citation"),
                                "reading_scope": "original_visual",
                            }, ensure_ascii=False)},
                            {"type": "image_url", "image_url": {"url": self._visual_urls[alias]}},
                        ])
        return [{"role": "user", "content": content}] if content else []

    def select_readings(self, token_budget, *, priority_after=0, estimator=None,
                        exclude=(), preferred=(), measure=None):
        """Select recently read complete native units for a capacity-driven new prefix.

        Versions at one position remain separate captured originals. Since existing
        material_version values describe individual passages, all captured members of
        a native unit travel together rather than splitting table rows by their hashes.
        Paginated originals retain the page context supplied at reading time.
        Members in ``exclude`` already have a readable home in the unabsorbed tail.
        ``measure`` can price the shared rendering inside the actual next request;
        standalone callers measure that rendering itself, never repeated snapshots.
        """
        estimator = estimator or TokenEstimator()
        excluded = set(exclude)
        preferred_units = {_unit(self.readings[handle]) for handle in preferred
                           if handle in self.readings}
        units = {}
        for alias, snapshot in self.readings.items():
            units.setdefault(_unit(snapshot), {})[alias] = snapshot
        order = sorted(
            units,
            key=lambda unit: (
                self._unit_read_at[unit] > priority_after, unit in preferred_units,
                self._unit_read_at[unit]
            ),
            reverse=True,
        )
        selected = {}
        for unit in order:
            companions = expand_captured_units(self.readings, units[unit])
            candidate = {**selected, **{alias: item for alias, item in companions.items()
                                       if alias not in excluded}}
            originals = render_originals(candidate)
            if measure:
                tokens = measure(originals)
            else:
                tokens = estimator.measure_texts([
                    json.dumps(originals, ensure_ascii=False)
                ])["input_tokens"]
                if visuals := self.visual_messages(originals):
                    tokens += estimator.measure(visuals)["input_tokens"]
            if tokens <= token_budget:
                selected = candidate
        return selected

    def rebase(self, messages, absorbed_through, *, shown_handles=()):
        """Install a completed manuscript prefix; the runtime then appends its tail.

        The supplied absorption watermark, not the current observation index, is the
        boundary. Events arriving during manuscript generation are still unsent and
        remain eligible for the runtime's ordinary incremental observation delivery.
        """
        self.messages = list(messages)
        self.observation_index = absorbed_through
        self.shown_handles = set(shown_handles)
