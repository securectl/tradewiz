"""
Skill registry — loads YAML skill definitions, provides lookup and tier gating.
"""

import os
import logging
import yaml

from skills.models import SkillDefinition

logger = logging.getLogger(__name__)

_catalog = {}  # skill_id -> SkillDefinition
_CATALOG_DIR = os.path.join(os.path.dirname(__file__), "catalog")

TIER_ORDER = {"free": 0, "basic": 1, "pro": 2, "admin": 3}

# Max concurrent jobs per tier
MAX_CONCURRENT = {
    "free": 1,
    "basic": 2,
    "pro": 3,
    "admin": 999,
}


def load_catalog():
    """Walk catalog/ directory and load all .yaml skill definitions."""
    global _catalog
    _catalog = {}
    if not os.path.isdir(_CATALOG_DIR):
        logger.warning("Skill catalog directory not found: %s", _CATALOG_DIR)
        return
    for root, _dirs, files in os.walk(_CATALOG_DIR):
        for fn in files:
            if not fn.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                if not data or "id" not in data:
                    continue
                skill = SkillDefinition(
                    id=data["id"],
                    name=data["name"],
                    domain=data.get("domain", ""),
                    description=data.get("description", ""),
                    tier_required=data.get("tier_required", "free"),
                    estimated_minutes=data.get("estimated_minutes", 5),
                    llm_calls_required=data.get("llm_calls_required", 1),
                    input_schema=data.get("input_schema", []),
                    output_types=data.get("output_types", ["json"]),
                    phases=data.get("phases", []),
                    dependencies=data.get("dependencies", []),
                    icon=data.get("icon", ""),
                )
                _catalog[skill.id] = skill
                logger.info("Loaded skill: %s", skill.id)
            except Exception as e:
                logger.error("Failed to load skill %s: %s", path, e)


def get_skill(skill_id):
    """Get a SkillDefinition by ID, or None."""
    return _catalog.get(skill_id)


def get_catalog(user_tier="free"):
    """Return skills available to the given tier."""
    tier_level = TIER_ORDER.get(user_tier, 0)
    result = []
    for skill in _catalog.values():
        req_level = TIER_ORDER.get(skill.tier_required, 0)
        result.append({
            "id": skill.id,
            "name": skill.name,
            "domain": skill.domain,
            "description": skill.description,
            "tier_required": skill.tier_required,
            "estimated_minutes": skill.estimated_minutes,
            "llm_calls_required": skill.llm_calls_required,
            "input_schema": skill.input_schema,
            "output_types": skill.output_types,
            "icon": skill.icon,
            "available": tier_level >= req_level,
        })
    return sorted(result, key=lambda s: (TIER_ORDER.get(s["tier_required"], 0), s["name"]))


def check_tier_access(skill_id, user_tier):
    """Return True if user tier can access this skill."""
    skill = get_skill(skill_id)
    if not skill:
        return False
    return TIER_ORDER.get(user_tier, 0) >= TIER_ORDER.get(skill.tier_required, 0)
