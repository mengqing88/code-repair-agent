class Policy:
    def __init__(self, rules):
        self.rules = list(rules)

    def allowed(self, role):
        decision = None
        for name, allow in self.rules:
            if name == role:
                decision = allow
        if decision is None:
            return False
        return decision
