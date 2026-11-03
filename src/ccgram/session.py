"""Agent session management.

Maintains runtime metadata for active tmux sessions (such as cached cwd).
Authoritative routing is owned by TmuxManager and ThreadRouter.
"""

from dataclasses import dataclass, field


@dataclass
class SessionManager:
    """Manages runtime session state."""

    _cwds: dict[str, str] = field(default_factory=dict)

    def set_window_cwd(self, window_id: str, cwd: str) -> None:
        """Set the working directory for a session."""
        self._cwds[window_id] = cwd

    def get_window_cwd(self, window_id: str) -> str:
        """Get the working directory for a session, or empty string."""
        return self._cwds.get(window_id, "")

    def clear_window_state(self, window_id: str) -> None:
        """Clear cached state for a window."""
        self._cwds.pop(window_id, None)

    def flush_state(self) -> None:
        """No-op kept for shutdown lifecycle compatibility."""
        pass


session_manager = SessionManager()

__all__ = [
    "SessionManager",
    "session_manager",
]
