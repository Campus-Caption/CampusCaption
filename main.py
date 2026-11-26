import sys

import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    cfg = get_settings()
    problem = cfg.bind_error()
    if problem:
        sys.exit("CampusCaption: " + problem)
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=True)
