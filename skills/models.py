"""
Skill data models — SkillDefinition and SkillJob.
"""

import time
import uuid
import threading
from dataclasses import dataclass, field


@dataclass
class SkillPhaseAction:
    type: str          # "fetch_fundamentals", "fetch_prices", "compute", "llm_call", "generate_output"
    description: str
    config: dict = field(default_factory=dict)


@dataclass
class SkillPhase:
    name: str
    description: str
    actions: list  # list of SkillPhaseAction dicts


@dataclass
class SkillDefinition:
    id: str
    name: str
    domain: str
    description: str
    tier_required: str          # "free", "basic", "pro"
    estimated_minutes: int
    llm_calls_required: int
    input_schema: list          # list of {name, type, label, required, default, options}
    output_types: list          # ["json", "docx", "xlsx"]
    phases: list                # list of SkillPhase dicts
    dependencies: list = field(default_factory=list)
    icon: str = ""


# Job status constants
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


@dataclass
class SkillJob:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: int = 0
    skill_id: str = ""
    inputs: dict = field(default_factory=dict)
    status: str = STATUS_PENDING
    progress: float = 0.0       # 0.0 to 1.0
    current_phase: str = ""
    message: str = ""
    result: dict = field(default_factory=dict)
    error: str = ""
    output_files: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    _listeners: list = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def notify(self, event_type="progress"):
        """Notify all SSE listeners of a state change (thread-safe)."""
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(self, event_type)
            except Exception:
                pass

    def add_listener(self, cb):
        with self._lock:
            self._listeners.append(cb)

    def remove_listener(self, cb):
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    def to_dict(self):
        with self._lock:
            return {
                "id": self.id,
                "user_id": self.user_id,
                "skill_id": self.skill_id,
                "inputs": self.inputs,
                "status": self.status,
                "progress": self.progress,
                "current_phase": self.current_phase,
                "message": self.message,
                "error": self.error,
                "output_files": list(self.output_files),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
            }
