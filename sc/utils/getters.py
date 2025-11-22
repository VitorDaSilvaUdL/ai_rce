
def get_rain(df: dict) -> dict:
    return df['info']['rain-prediction']


def get_temp(df) -> dict:
    return df['info']['tank-temperature']


def get_prod(df) -> dict:
    return df['info']['energy-production']


def get_dem(df) -> dict:
    print(type(df))
    return df['info']['demand']


def get_cold(df, type) -> dict:
    if type == 1:
        return df['cooling']
    return df['cold']


def get_hot(df, type) -> dict:
    if type == 1:
        return df['heating']
    return df['hot']