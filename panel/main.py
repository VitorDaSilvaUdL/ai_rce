"""Entry point of the Panel RCE: supervisor of api.main and sc.main plus the web page.

    python -m panel.main            (SC_ENV=prod is set by deploy/run_panel.cmd)

Listens on panel_config.json bind:port (default 0.0.0.0:8050). Starts the API and the control if autostart is on.
"""
import os
import sys
from pathlib import Path

import uvicorn

from panel.collector import Collector
from panel.config import Config
from panel.readers import api_health
from panel.supervisor import ManagedProcess, Supervisor
from panel.web import PanelContext, create_app
from sc import config as sc_config
from sc import state_io


def build_context():
    """PANEL_WORK_DIR (optional): folder for state/, logs, sc.log and heartbeat instead of the project folder
    (used to run the whole stack locally without writing into the repository)."""
    root = sc_config.PROJECT_DIR
    work = Path(os.getenv("PANEL_WORK_DIR") or root)
    state_dir = work / "state" if os.getenv("PANEL_WORK_DIR") else state_io.STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(state_dir / "panel_config.json")
    logs = work / "logs"
    env = {"SC_ENV": os.getenv("SC_ENV", "prod"), "PYTHONPATH": str(root)}
    if os.getenv("SC_CONFIG"):
        env["SC_CONFIG"] = os.getenv("SC_CONFIG")
    if os.getenv("PANEL_WORK_DIR"):
        env["SC_STATE_DIR"] = str(state_dir)
    api = ManagedProcess("api", [sys.executable, "-m", "api.main"], cwd=root, log_path=logs / "api.log", env=env)
    sc = ManagedProcess("sc", [sys.executable, "-m", "sc.main"], cwd=work, log_path=logs / "sc_process.log", env=env)
    health = lambda: api_health(cfg.get("api_url") + "/health")
    sup = Supervisor(api=api, sc=sc, api_ready=health)
    return PanelContext(
        root=root, state_dir=state_dir, wincc_dir=Path(sc_config.LECT_DIR).parent,
        dades_path=sc_config.OUTPUT_CSV_PATH, log_path=work / "sc.log", heartbeat_path=work / "heartbeat_sc.txt",
        config=cfg, supervisor=sup, mode=sc_config.PLC_MODE, api_health=health,
        plc={"ip": sc_config.PLC_IP, "rack": sc_config.PLC_RACK, "slot": sc_config.PLC_SLOT},
        sc_config_path=sc_config.CONFIG_FILE,
    )


def main():
    ctx = build_context()
    app = create_app(ctx)
    ctx.collector = Collector(state_dir=ctx.state_dir, readings=lambda hours: ctx.readings(hours=hours, max_bytes=8_000_000),
                              decisions=lambda since: __import__("panel.readers", fromlist=["read_decisions"]).read_decisions(ctx.dades_path, since),
                              diary=ctx.diary, mode=lambda: ctx.mode, probe=lambda: ctx.probe_cached(55))
    ctx.collector.run_in_thread()
    if ctx.config.get("autostart"):
        ctx.supervisor.start()
    ctx.supervisor.run_in_thread(interval_s=5)
    try:
        uvicorn.run(app, host=ctx.config.get("bind"), port=int(ctx.config.get("port")), log_level="info")
    finally:
        ctx.supervisor.shutdown()


if __name__ == "__main__":
    main()
