# 促销结算：先按 discount_bp 打折，再减去优惠券金额，结果不低于 0。
# 所有金额单位为分，discount_bp 为百分点。


def final_price(price, discount_bp, coupon):
    base = price - coupon
    discounted = (base * discount_bp + 50) // 100
    return max(0, discounted)
