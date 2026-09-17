from __future__ import annotations

from threading import RLock


# All writes use this lock so a bulk rollback cannot overwrite a newer
# single-setting update from another request.
internal_session_visibility_lock = RLock()
