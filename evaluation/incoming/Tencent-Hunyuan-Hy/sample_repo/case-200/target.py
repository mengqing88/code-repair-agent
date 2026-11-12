# 积分兑换：扣减积分后返回余额，余额不得低于 0。


def redeem(balance, cost):
    if balance < 0:
        balance = 0
    return balance - cost
