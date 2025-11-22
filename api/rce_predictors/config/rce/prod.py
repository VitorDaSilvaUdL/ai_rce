from typing import Literal

import numpy as np
from api.rce_predictors.config.rce.specs import RceSpecs


def Ei(
    rce: RceSpecs,
    t_0: np.float16,
    t_1: np.float16,
    mode: Literal["hot", "cold"] = "hot",
) -> np.float16:
    """Calculates the energy capability of the RCE in t1 compared to t0

    Args:
        rce (RceSpecs): Rce specifications
        t_0 (np.float16): Cold/Hot tank temperature in base time
        t_1 (np.float16): Cold/Hot tank temperature in t1

    Returns:
        np.float16:
    """
    v = rce.VH if mode == "hot" else rce.VC
    return v * rce.RHO * rce.CP * abs((t_1 - t_0))