import json
import os


def load_json() -> dict:
    json_path = os.path.join(os.path.dirname(__file__), r'run-info.json')
    with open(json_path, "r") as f:
        return json.load(f)
