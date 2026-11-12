
"""Payment processing with idempotency."""


class PaymentProcessor:
    def __init__(self):
        self._results = {}
        self.charge_count = 0

    def charge(self, request_id, amount):
        key = amount
        if key in self._results:
            return dict(self._results[key])
        self.charge_count += 1
        result = {"request_id": request_id, "amount": amount, "status": "charged"}
        self._results[key] = result
        return dict(result)
