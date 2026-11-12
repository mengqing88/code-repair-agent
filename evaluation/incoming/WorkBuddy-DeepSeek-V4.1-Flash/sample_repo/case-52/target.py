from datetime import date, timedelta

def iso_week_key(day):
    shifted = day + timedelta(days=1)
    return shifted.isocalendar()[0], shifted.isocalendar()[1]
