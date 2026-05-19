"""
Configuration Persistence
-------------------------
Handle saving and loading of block configurations.
Only depends on core/.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from ui.core.config import CONFIG_JSON_DIR, BASE_DIR


class ConfigPersistence:
    """
    Handle persistence of block configurations to JSON files.
    """
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or CONFIG_JSON_DIR
        self.config_dir.mkdir(exist_ok=True)
    
    def save(self, block_id: str, data: Dict[str, Any]) -> Path:
        """
        Save block configuration to JSON file.
        
        Args:
            block_id: The block identifier (e.g., "ConfigInit")
            data: The configuration data to save
            
        Returns:
            Path to the saved file
        """
        file_path = self.config_dir / f"{block_id}.json"
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return file_path
    
    def load(self, block_id: str) -> Optional[Dict[str, Any]]:
        """
        Load block configuration from JSON file.
        
        Args:
            block_id: The block identifier
            
        Returns:
            Configuration dict or None if not found
        """
        file_path = self.config_dir / f"{block_id}.json"
        if not file_path.exists():
            return None
        
        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Handle wrapped format {"config": {...}}
            if "config" in data and isinstance(data["config"], dict):
                return data["config"]
            return data
        except (json.JSONDecodeError, IOError):
            return None
    
    def list_files(self, block_id: str) -> List[Dict[str, str]]:
        """
        List configuration files for a block.
        
        Args:
            block_id: The block identifier (used as prefix)
            
        Returns:
            List of dicts with 'label' and 'value' keys
        """
        pattern = f"{block_id}*.json" if block_id else "*.json"
        options: List[Dict[str, str]] = []
        
        for path in sorted(self.config_dir.glob(pattern)):
            if path.is_file():
                options.append({"label": path.name, "value": path.name})
        
        return options
    
    def format_path(self, path: Path) -> str:
        """Return a friendly path relative to the project base directory."""
        try:
            return str(path.relative_to(BASE_DIR))
        except ValueError:
            return str(path)


# Singleton instance
config_persistence = ConfigPersistence()
