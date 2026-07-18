from __future__ import annotations

import uvicorn

from app.application import create_app
from app.core.config import get_settings


settings = get_settings()
app = create_app(settings)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
    )
