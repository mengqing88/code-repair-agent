from markupsafe import Markup
assert Markup('a <!-- hidden --> b').striptags() == 'a b'
