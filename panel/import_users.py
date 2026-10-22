"""Import the team into the panel: one user per line (university e-mail or user name; # comments allowed).

    python -m panel.import_users usuarios.txt

New users get their own user name as initial password and must change it at the first login. Users that already
exist are not touched. The panel can be running. Uses the same state folder as the panel (PANEL_WORK_DIR or state/).
"""
import os
import sys
from pathlib import Path

from panel.auth import People


def default_state_dir():
    if os.getenv("PANEL_WORK_DIR"):
        return Path(os.getenv("PANEL_WORK_DIR")) / "state"
    from sc import state_io
    return state_io.STATE_DIR


def main(argv=None, state_dir=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print(__doc__.strip())
        print(f"\nNo encuentro el fichero de usuarios: {argv[0] if argv else '(falta)'}")
        return 2
    state = Path(state_dir) if state_dir else default_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    people = People(state / "panel_config.json")
    created, skipped = people.import_users(Path(argv[0]).read_text(encoding="utf-8").splitlines())
    for name in created:
        print(f"dado de alta: {name} (contraseña inicial: su usuario; la cambiará al entrar)")
    for name in skipped:
        print(f"ya existía, sin cambios: {name}")
    print(f"{len(created)} dados de alta, {len(skipped)} sin cambios. Personas en el panel: {', '.join(people.names())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
