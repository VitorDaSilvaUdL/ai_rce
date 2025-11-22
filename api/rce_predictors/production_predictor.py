from pandas import DataFrame
from api.rce_predictors.base_predictor import IDatedPredictor
from api.rce_predictors.config.rce.prod import Ei
from api.rce_predictors.config.rce.specs import RceSpecs
import logging

logger = logging.getLogger(__name__)

class ProductionPredictor(IDatedPredictor):

    def __init__(self, rce_specs: RceSpecs) -> None:
        super().__init__()
        self._specs = rce_specs

    def __repr__(self) -> str:
        return "\n\t".join(
            [
                f"{self.__class__.__name__}",
                f"{self._specs}",
            ]
        )

    def predict(self, data: DataFrame) -> DataFrame:
        """Calculates the future stored energy comparing the temperature
        of the tanks in the future instant t with the current state

        Args:
            parameters (DataFrame): Containing the current state in idx
            0 and the future ones in >0.

        Returns:
            DataFrame: containing the predictions with the resulting
            production capabilities
        """
        logger.info("START Production Prediction")
        data["prod_cold"] = data.iloc[1:].apply(
            lambda x: Ei(
                self._specs,
                t_0=data.iloc[0]["cold"],
                t_1=x["cold"],
                mode="cold",
            ),
            axis=1,
        )
        logger.debug("Cold production OK")
        data["prod_hot"] = data.iloc[1:].apply(
            lambda x: Ei(
                self._specs,
                t_0=data.iloc[0]["hot"],
                t_1=x["hot"],
                mode="hot",
            ),
            axis=1,
        )
        logger.debug("Hot production OK")
        logger.info("END Production Prediction")
        return (
            data.iloc[1:]
            .drop(columns=["cold", "hot"])
            .rename(
                columns={
                    "prod_hot": "hot",
                    "prod_cold": "cold",
                }
            )
        )
