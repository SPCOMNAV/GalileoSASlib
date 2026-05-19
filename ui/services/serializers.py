"""
Data Serializers
----------------
Functions for converting data types for storage and display.
Only depends on core/.
"""

from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


def serialize_for_store(value: Any) -> Any:
    """
    Serialize a value for Dash dcc.Store components.
    
    Handles common types that need conversion:
    - Path -> str
    - datetime -> ISO format string
    - numpy arrays -> lists
    - dataclasses -> dicts
    - objects with to_dict() method
    
    Args:
        value: Any value to serialize
        
    Returns:
        Serialized value safe for JSON storage
    """
    if isinstance(value, Path):
        return str(value)
    
    if isinstance(value, datetime):
        return value.isoformat()
    
    if isinstance(value, np.ndarray):
        return value.tolist()
    
    if isinstance(value, (list, tuple)):
        return [serialize_for_store(v) for v in value]
    
    if isinstance(value, dict):
        return {k: serialize_for_store(v) for k, v in value.items()}
    
    if is_dataclass(value):
        return asdict(value)
    
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    
    return value


def to_plain_dict(obj: Any) -> Any:
    """
    Convert an object to a plain dict if possible.
    
    Args:
        obj: Object to convert
        
    Returns:
        Plain dict or original object
    """
    if is_dataclass(obj):
        return asdict(obj)
    
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    
    if isinstance(obj, dict):
        return obj
    
    return obj


def preview_param_value(value: Any) -> str:
    """
    Create a preview string for displaying parameter values.
    
    Used in block labels to show compact parameter info.
    
    Args:
        value: Parameter value to preview
        
    Returns:
        Compact string representation
    """
    import math
    
    if value is None:
        return "—"
    
    if isinstance(value, dict):
        return f"dict({len(value)})"
    
    if isinstance(value, (list, tuple)):
        return f"list({len(value)})"
    
    if isinstance(value, str):
        txt = value.strip()
        if len(txt) > 32:
            return txt[:29] + "..."
        return txt
    
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "NaN"
        rounded = round(value, 3)
        return f"{rounded}".rstrip("0").rstrip(".")
    
    return str(value)
