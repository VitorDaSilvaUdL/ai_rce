from datetime import datetime

limit = 0.7

class TimeSelector:
    """
    Class to select which quarter of the day the current time is
    :var TIMES: List of time marks where keys are times and values the quarter of the day
    :var MED: The medium times between the times there was a call to the rain API
    """

    TIMES = {"0208": 0, "0814": 1, "1420": 2, "2002": 3}

    def __init__(self):
        pass

    def select(self, time: datetime):
        """
        Selects the quarter of the day based on the current time
        :param time:
        :return:
        """
        time_marks = list(self.TIMES.keys())
        i = 0
        while i < len(time_marks)-1:
            left = datetime.strptime(time_marks[i], "%H%M")
            right = datetime.strptime(time_marks[i+1], "%H%M")
            if left <= time <= right:
                return i
            i += 1
        return 3

def current_time():
    """
    Gets the current time
    :return: The current time in format DD-MM-YYYY HH:MM
    """
    now = datetime.now()
    time_str = now.strftime("%H%M")
    return datetime.strptime(time_str, "%H%M")



def select_rain_prediction():
    time_selector = TimeSelector()

    ct = current_time()
    return time_selector.select(ct)

def select_option(prediction):
    quart = select_rain_prediction()
    values = list(prediction.values())
    pred_i = values[quart]
    if pred_i >= 0.7:
        return 1
    return 0


