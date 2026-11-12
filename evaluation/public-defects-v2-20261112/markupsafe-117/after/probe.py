from markupsafe import Markup
assert Markup('&NotEqualTilde;').unescape() == '\u2242\u0338'
