class OutputBuilder:
    def __init__(self) -> None:
        self._out = {
            "info": {},
        }

    def build(self) -> dict:
        return self._out.copy()

    def add(self, key: str, value: any) -> "OutputBuilder":
        self._out[key] = value
        return self

    def add_data(self, key: str, value: any) -> "OutputBuilder":
        self._out["info"][key] = value
        return self

    def add_exception(self, value: str) -> "OutputBuilder":
        self._out["info"]["explanation"] = value
        return self


def unexpected_data_length(
    actual: int,
    expected: int,
    fetched: bool,
) -> str:
    return " ".join(
        [
            "Fetched" if fetched else "Passed",
            f"data is not the expected length (expected: {expected} != {actual})",
        ]
    )
