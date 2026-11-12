from markupsafe import Markup
assert Markup("text<!-- hidden\ncomment --><meta content='a\nb'>").striptags() == 'text'
