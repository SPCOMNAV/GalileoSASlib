"""
UI Services Module
------------------
Service layer that wraps external dependencies (SASpipeline).
Only depends on core/.
"""

from .pipeline import PipelineService, pipeline_service
from .persistence import ConfigPersistence, config_persistence
from .serializers import serialize_for_store, to_plain_dict

__all__ = [
    "PipelineService", "pipeline_service",
    "ConfigPersistence", "config_persistence",
    "serialize_for_store", "to_plain_dict",
]
