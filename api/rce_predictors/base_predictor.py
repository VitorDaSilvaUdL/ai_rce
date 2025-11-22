from abc import ABCMeta, abstractmethod

import pandas as pd


class IDatedPredictor(metaclass=ABCMeta):
    @abstractmethod
    def predict(self, parameters: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError(
            f"Use of base class {self.__class__.__name__} predict method"
        )

    @classmethod
    def __subclasshook__(cls, __subclass: type) -> bool:
        return (
            hasattr(__subclass, "predict")
            and callable(__subclass.predict)
            or NotImplemented
        )
