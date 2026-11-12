def rank_students(students):
    return sorted(students, key=lambda s: s[1], reverse=True)
