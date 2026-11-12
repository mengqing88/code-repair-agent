def billable_hours(start_minute, end_minute):
    if not 0 <= start_minute <= 1439 or not 0 <= end_minute <= 1439:
        raise ValueError('minute must be within a day')
    span = (end_minute - start_minute) % 1440
    return span // 60
