import re

def valid_code(value):
    return bool(re.match(r'[A-Z]{3}', value))
