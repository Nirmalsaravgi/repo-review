from payments import charge


class OrderService:
    def create(self) -> int:
        return charge(10)
