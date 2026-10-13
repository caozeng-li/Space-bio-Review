"""Stable delivery entry point; the publication module owns new report generation."""

from .publication_delivery import Delivery, TextDraft

__all__ = ["Delivery", "TextDraft"]
