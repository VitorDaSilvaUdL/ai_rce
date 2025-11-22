from dataclasses import dataclass, fields

import numpy as np


@dataclass(frozen=True)
class RceSpecs:
    """Virtualization of the RCE specifications"""

    volume_cold: np.float16
    """Volume of the tank that retains the cold water in m3"""

    volume_hot: np.float16
    """Volume of the tank that retains the hot water in m3"""

    water_density: np.float16 = 1000
    """Density of the water inside the RCE, defaults to 1000 kg/m3"""

    specific_heat_capacity: np.float16 = 4192
    """Specific heat capacity of the water, defaults to 4192 kJ/kg/ºC"""

    def __post__init__(self):
        for field in fields(self):
            # If there is a default and the value of the field is none,
            # assign a value
            if (
                not isinstance(field.default, dataclass._MISSING_TYPE)
                and getattr(self, field.name) is None
            ):
                setattr(self, field.name, field.default)

    @property
    def VH(self) -> np.float16:
        return self.volume_hot

    @property
    def VC(self) -> np.float16:
        return self.volume_cold

    @property
    def RHO(self) -> np.float16:
        return self.water_density

    @property
    def CP(self) -> np.float16:
        return self.specific_heat_capacity
