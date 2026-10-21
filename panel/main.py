"""Entry point of the Panel RCE: supervisor of api.main and sc.main plus the web page.

    python -m panel.main            (SC_ENV=prod is set by deploy/run_panel.cmd)

Listens on panel_config.json bind:port (default 0.0.0.0:8050). Starts the API and the control if autostart is on.
"""
import os
import sys
from pathlib import Path

import uvicorn

from panel.config import Config
from panel.readers import api_health
from panel.supervisor import ManagedProcess, Supervisor
from panel.web import PanelContext, create_app
from sc import config as sc_config
from sc import state_io


def build_context():
    root = sc_config.PROJECT_DIR
    state_dir = state_io.STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(state_dir / "panel_config.json")
    logs = root / "logs"
    env = {"SC_ENV": os.getenv("SC_ENV", "prod")}
    api = ManagedProcess("api", [sys.executable, "-m", "api.main"], cwd=root, log_path=logs / "api.log", env=env)
    sc = ManagedProcess("sc", [sys.executable, "-m", "sc.main"], cwd=root, log_path=logs / "sc_process.log", env=env)
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: api_health(cfg.get("api_url") + "/health"))
    return PanelContext(
        root=root, state_dir=state_dir, wincc_dir=Path(sc_config.LECT_DIR).parent,
        dades_path=sc_config.OUTPUT_CSV_PATH, log_path=root / "sc.log", heartbeat_path=root / "heartbeat_sc.txt",
        config=cfg, supervisor=sup, mode=sc_config.PLC_MODE,
        api_health=lambda: api_health(cfg.get("api_url") + "/health"),
    )


def main():
    ctx = build_context()
    app = create_app(ctx)
    if ctx.config.get("autostart"):
        ctx.supervisor.start()
    ctx.supervisor.run_in_thread(interval_s=5)
    try:
        uvicorn.run(app, host=ctx.config.get("bind"), port=int(ctx.config.get("port")), log_level="info")
    finally:
        ctx.supervisor.shutdown()


if __name__ == "__main__":
    main()
