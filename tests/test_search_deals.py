import unittest

from search_deals import (
    DealItem,
    best_yahoo_discount,
    calc_discount_rate,
    coupon_from_text,
    discount_rate_from_text,
    point_rate_from_text,
    score_item,
    yahoo_point_rate_and_amount,
)


class DealScoringTest(unittest.TestCase):
    def test_calc_discount_rate(self):
        self.assertEqual(calc_discount_rate(10000, 7000), 30.0)
        self.assertIsNone(calc_discount_rate(7000, 10000))

    def test_yahoo_discount_uses_best_reference(self):
        hit = {
            "price": 7000,
            "priceLabel": {
                "defaultPrice": 10000,
                "fixedPrice": 12000,
                "discountedPrice": 7000,
            },
        }
        self.assertEqual(best_yahoo_discount(hit), (41.7, "fixedPrice"))

    def test_yahoo_points_prefers_current_ly_fields(self):
        hit = {
            "price": 10000,
            "point": {
                "bonusTimes": 0,
                "lyLimitedBonusTimes": 12,
                "lyLimitedBonusAmount": 1200,
            },
        }
        self.assertEqual(yahoo_point_rate_and_amount(hit), (12.0, 1200))

    def test_rakuten_text_discount(self):
        self.assertEqual(discount_rate_from_text("期間限定 半額 セール"), (50.0, "text:半額/50%OFF"))
        self.assertEqual(discount_rate_from_text("今だけ30%OFF"), (30.0, "text"))

    def test_rakuten_text_points(self):
        self.assertEqual(point_rate_from_text("7/1限定 P10倍"), 10.0)
        self.assertEqual(point_rate_from_text("ポイント 20倍 キャンペーン"), 20.0)
        self.assertEqual(point_rate_from_text("10倍ポイント"), 10.0)

    def test_coupon_from_text(self):
        self.assertEqual(coupon_from_text("最大10%OFFクーポン&P10倍")[:3], (True, 10.0, None))
        self.assertEqual(coupon_from_text("クーポンで1,000円OFF")[:3], (True, None, 1000))
        self.assertEqual(coupon_from_text("限定半額クーポン")[:3], (True, 50.0, None))

    def test_score_combines_discount_points_and_sale(self):
        item = DealItem(
            source="yahoo",
            name="test",
            price=1000,
            url="https://example.com",
            discount_rate=20,
            point_rate=10,
            sale_end="1893456000",
        )
        scored = score_item(item, min_discount_rate=20, min_point_rate=5)
        self.assertEqual(scored.score, 33.0)
        self.assertIn("20% off", scored.reason)
        self.assertIn("10x", scored.reason)


if __name__ == "__main__":
    unittest.main()
