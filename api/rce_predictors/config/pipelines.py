import os
from dataclasses import dataclass

import dill
import numpy as np
from sklearn.pipeline import Pipeline


@dataclass(frozen=True)
class ReversablePipeline:
    """Class to store pipeline info"""

    name: str
    pipeline: Pipeline
    reversed: Pipeline
    fit: bool = False


class MultiPipeline:
    """Pipeline wrapper that allows us to use a configuration file to build a multi-pipeline pipeline and be reversed."""

    def __init__(self):
        self._pipelines: list[ReversablePipeline] = []

    def load_config(self, config_file: str) -> "MultiPipeline":
        """Loads the MultiPipeline object properties from the given configuration file path

        Args:
            config_file (str): path to the configuration path

        Returns:
            MultiPipeline: self
        """

        # 1. Read given configuration file
        config = load_json()

        # 2. Load the pipelines from the given configuration file
        pipelines = []
        pipelines_path = os.path.dirname(__file__)

        # 3. Build each pipeline stated in the config file
        for k, v in config.items():
            with open(os.path.join(pipelines_path, v["name"]), "rb") as f:
                _pipeline = dill.load(f)

            if v["reversed"]:
                with open(
                    os.path.join(pipelines_path, v["reversed"]), "rb"
                ) as f:
                    _reversed = dill.load(f)
            else:
                _reversed = None

            pipelines.insert(
                int(k),
                ReversablePipeline(
                    name=v["name"],
                    pipeline=_pipeline,
                    reversed=_reversed,
                    fit=v["fit"],
                ),
            )

        # 4. Update the object if nothing has failed
        self._pipelines = pipelines
        return self

    def fit_transform(self, X, reversed=False):
        """Apply the stored pipeline objects on the given data.

        Args:
            X: data
            reversed (bool, optional): use the MultiPipeline in reversed mode. Defaults to False.

        Returns:
            data: transformed data
        """
        print(
            f"Applying {self.__class__.__name__} on",
            "normal..." if not reversed else "reverse...",
        )

        for idx, rp in enumerate(self._pipelines[:: -1 if reversed else 1]):
            print(
                f"\t[{idx}] Applying {rp.name} ({'' if rp.fit else 'not'} fit)... ",
                end="",
            )
            if reversed:
                if rp.reversed is None:
                    print("Skipping due to not specified reverse step")
                    continue
                X = rp.reversed.transform(X)
            elif not rp.fit:
                X = rp.pipeline.transform(X)
            else:
                X = rp.pipeline.fit_transform(X)
            print("OK")
        print(f"Finished applying {self.__class__.__name__}")
        return X


def load_json():
    import json


    print(os.path.dirname(__file__))

    config_path = os.path.join(os.path.dirname(__file__), r'config-full.json')
    print(config_path)
    with open(config_path, 'r') as f:
        file_content = f.read()

    return json.loads(file_content)