#!/usr/bin/env python3
"""Search Rakuten Ichiba and Yahoo Shopping for discounted or high-point items."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


RAKUTEN_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
YAHOO_ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"

RAKUTEN_ELEMENTS = ",".join(
    [
        "itemName",
        "catchcopy",
        "itemCaption",
        "itemCode",
        "itemPrice",
        "itemUrl",
        "shopName",
        "reviewCount",
        "reviewAverage",
        "affiliateRate",
        "startTime",
        "endTime",
        "pointRate",
        "pointRateStartTime",
        "pointRateEndTime",
        "postageFlag",
        "mediumImageUrls",
    ]
)


@dataclass
class DealItem:
    source: str
    name: str
    price: int | None
    url: str
    shop: str | None = None
    code: str | None = None
    discount_rate: float | None = None
    discount_basis: str | None = None
    point_rate: float | None = None
    point_amount: int | None = None
    coupon_available: bool = False
    coupon_discount_rate: float | None = None
    coupon_amount: int | None = None
    coupon_text: str | None = None
    affiliate_rate: float | None = None
    review_count: int | None = None
    review_average: float | None = None
    shipping: str | None = None
    sale_start: str | None = None
    sale_end: str | None = None
    image_url: str | None = None
    reason: str = ""
    score: float = 0.0

    @property
    def effective_price(self) -> float | None:
        if self.price is None:
            return None
        point_rate = self.point_rate or 0.0
        return max(0.0, self.price * (1.0 - point_rate / 100.0))


class ApiError(RuntimeError):
    pass


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_json(url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request_url = f"{url}?{query}"
    request = urllib.request.Request(
        request_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "deal-search/0.1",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Request failed for {url}: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Non-JSON response from {url}: {body[:500]}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise ApiError(f"{payload.get('error')}: {payload.get('error_description', '')}")
    return payload


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_image_url(value: Any) -> str | None:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return first.get("imageUrl")
        if isinstance(first, str):
            return first
    if isinstance(value, dict):
        return value.get("medium") or value.get("small") or value.get("url")
    return None


def calc_discount_rate(reference_price: int | None, current_price: int | None) -> float | None:
    if not reference_price or not current_price or reference_price <= current_price:
        return None
    return round((reference_price - current_price) / reference_price * 100, 1)


def best_yahoo_discount(hit: dict[str, Any]) -> tuple[float | None, str | None]:
    price = as_int(hit.get("price"))
    price_label = hit.get("priceLabel") or {}
    discounted = as_int(price_label.get("discountedPrice")) or price
    default_price = as_int(price_label.get("defaultPrice"))
    fixed_price = as_int(price_label.get("fixedPrice"))
    premium_discount = as_float(hit.get("premiumDiscountRate"))

    candidates: list[tuple[float, str]] = []
    for label, reference in [("defaultPrice", default_price), ("fixedPrice", fixed_price)]:
        rate = calc_discount_rate(reference, discounted)
        if rate is not None:
            candidates.append((rate, label))
    if premium_discount and premium_discount > 0:
        candidates.append((round(premium_discount, 1), "premiumDiscountRate"))

    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[0])


def yahoo_point_rate_and_amount(hit: dict[str, Any], premium: bool = False) -> tuple[float | None, int | None]:
    point = hit.get("point") or {}
    rate_fields = ["lyLimitedBonusTimes", "bonusTimes", "times"]
    amount_fields = ["lyLimitedBonusAmount", "bonusAmount", "amount"]
    if premium:
        rate_fields = ["lyLimitedPremiumBonusTimes", "premiumBonusTimes", "premiumTimes", *rate_fields]
        amount_fields = ["lyLimitedPremiumBonusAmount", "premiumBonusAmount", "premiumAmount", *amount_fields]

    rates = [as_float(point.get(field)) for field in rate_fields]
    amounts = [as_int(point.get(field)) for field in amount_fields]
    rate = max([value for value in rates if value is not None], default=None)
    amount = max([value for value in amounts if value is not None], default=None)

    price = as_int(hit.get("price"))
    if price and amount and amount > 0:
        amount_rate = amount / price * 100
        rate = max(rate or 0.0, amount_rate)
    return (round(rate, 1) if rate is not None else None, amount)


PERCENT_PATTERNS = [
    re.compile(r"(\d{1,2})\s*[%％]\s*(?:OFF|オフ|割引|引き)", re.IGNORECASE),
    re.compile(r"(?:OFF|オフ|割引|引き)\s*(\d{1,2})\s*[%％]", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*割引"),
]

POINT_PATTERNS = [
    re.compile(r"(?:P|ポイント)\s*(\d{1,3})\s*倍", re.IGNORECASE),
    re.compile(r"(\d{1,3})\s*倍\s*(?:P|ポイント)", re.IGNORECASE),
]

COUPON_RATE_PATTERNS = [
    re.compile(r"(\d{1,2})\s*[%％]\s*(?:OFF|オフ|割引|引き)?\s*クーポン", re.IGNORECASE),
    re.compile(r"クーポン(?:で|利用で|使用で)?\s*(\d{1,2})\s*[%％]\s*(?:OFF|オフ|割引|引き)", re.IGNORECASE),
]

COUPON_AMOUNT_PATTERNS = [
    re.compile(r"([0-9,]{2,7})\s*円\s*(?:OFF|オフ|割引|引き)?\s*クーポン", re.IGNORECASE),
    re.compile(r"クーポン(?:で|利用で|使用で)?\s*([0-9,]{2,7})\s*円\s*(?:OFF|オフ|割引|引き)", re.IGNORECASE),
]


def discount_rate_from_text(*parts: str | None) -> tuple[float | None, str | None]:
    text = " ".join(part for part in parts if part)
    if not text:
        return None, None
    if "半額" in text or "50%OFF" in text.upper() or "50％OFF" in text.upper():
        return 50.0, "text:半額/50%OFF"

    rates: list[float] = []
    for pattern in PERCENT_PATTERNS:
        for match in pattern.finditer(text):
            value = as_float(match.group(1))
            if value is not None and 0 < value < 100:
                rates.append(value)
    if not rates:
        return None, None
    return max(rates), "text"


def point_rate_from_text(*parts: str | None) -> float | None:
    text = " ".join(part for part in parts if part)
    if not text:
        return None

    rates: list[float] = []
    for pattern in POINT_PATTERNS:
        for match in pattern.finditer(text):
            value = as_float(match.group(1))
            if value is not None and 0 < value < 100:
                rates.append(value)
    if not rates:
        return None
    return max(rates)


def coupon_from_text(*parts: str | None) -> tuple[bool, float | None, int | None, str | None]:
    text = " ".join(part for part in parts if part)
    if not text or "クーポン" not in text:
        return False, None, None, None

    rate: float | None = None
    amount: int | None = None
    if "半額クーポン" in text or "クーポンで半額" in text:
        rate = 50.0

    for pattern in COUPON_RATE_PATTERNS:
        for match in pattern.finditer(text):
            value = as_float(match.group(1))
            if value is not None and 0 < value < 100:
                rate = max(rate or 0.0, value)

    for pattern in COUPON_AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            value = as_int(match.group(1).replace(",", ""))
            if value is not None and value > 0:
                amount = max(amount or 0, value)

    index = text.find("クーポン")
    start = max(0, index - 28)
    end = min(len(text), index + 42)
    snippet = text[start:end].strip()
    return True, rate, amount, snippet


def score_item(item: DealItem, min_discount_rate: float, min_point_rate: float) -> DealItem:
    reasons: list[str] = []
    discount = item.discount_rate or 0.0
    points = item.point_rate or 0.0

    if discount >= min_discount_rate:
        reasons.append(f"{discount:g}% off")
    elif discount > 0:
        reasons.append(f"{discount:g}% off text")

    if points >= min_point_rate:
        reasons.append(f"{points:g}x/≈{points:g}% points")
    elif points > 0:
        reasons.append(f"{points:g}x points")

    if item.coupon_discount_rate:
        reasons.append(f"{item.coupon_discount_rate:g}% coupon")
    elif item.coupon_amount:
        reasons.append(f"{item.coupon_amount:,} yen coupon")
    elif item.coupon_available:
        reasons.append("coupon text")

    if item.sale_start or item.sale_end:
        reasons.append("limited sale")

    if item.affiliate_rate and item.affiliate_rate >= 5:
        reasons.append(f"{item.affiliate_rate:g}% affiliate")

    sale_bonus = 3.0 if item.sale_start or item.sale_end else 0.0
    item.score = round(discount + points + sale_bonus, 1)
    item.reason = "; ".join(reasons)
    return item


def search_rakuten(
    keyword: str,
    limit: int,
    min_price: int | None,
    max_price: int | None,
    sort: str,
    min_discount_rate: float,
    min_point_rate: float,
    pause: float,
) -> list[DealItem]:
    application_id = os.getenv("RAKUTEN_APPLICATION_ID")
    access_key = os.getenv("RAKUTEN_ACCESS_KEY")
    if not application_id or not access_key:
        print("skip Rakuten: set RAKUTEN_APPLICATION_ID and RAKUTEN_ACCESS_KEY", file=sys.stderr)
        return []

    affiliate_id = os.getenv("RAKUTEN_AFFILIATE_ID") or None
    referer = os.getenv("RAKUTEN_REFERER") or None
    rakuten_headers = {}
    if referer:
        rakuten_headers["Referer"] = referer
        parsed_referer = urllib.parse.urlparse(referer)
        if parsed_referer.scheme and parsed_referer.netloc:
            rakuten_headers["Origin"] = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
    results: list[DealItem] = []
    page = 1

    while len(results) < limit and page <= 100:
        hits = min(30, limit - len(results))
        params = {
            "applicationId": application_id,
            "accessKey": access_key,
            "affiliateId": affiliate_id,
            "format": "json",
            "formatVersion": 2,
            "keyword": keyword,
            "hits": hits,
            "page": page,
            "availability": 1,
            "sort": sort,
            "minPrice": min_price,
            "maxPrice": max_price,
            "elements": RAKUTEN_ELEMENTS,
        }
        payload = get_json(RAKUTEN_ENDPOINT, params, headers=rakuten_headers)
        items = payload.get("Items") or payload.get("items") or []
        if not items:
            break

        for raw_item in items:
            item = raw_item.get("Item", raw_item) if isinstance(raw_item, dict) else {}
            discount_rate, discount_basis = discount_rate_from_text(
                item.get("catchcopy"),
                item.get("itemName"),
                item.get("itemCaption"),
            )
            api_point_rate = as_float(item.get("pointRate"))
            text_point_rate = point_rate_from_text(
                item.get("catchcopy"),
                item.get("itemName"),
                item.get("itemCaption"),
            )
            coupon_available, coupon_rate, coupon_amount, coupon_text = coupon_from_text(
                item.get("catchcopy"),
                item.get("itemName"),
                item.get("itemCaption"),
            )
            deal = DealItem(
                source="rakuten",
                name=str(item.get("itemName") or ""),
                price=as_int(item.get("itemPrice")),
                url=str(item.get("affiliateUrl") or item.get("itemUrl") or ""),
                shop=item.get("shopName"),
                code=item.get("itemCode"),
                discount_rate=discount_rate,
                discount_basis=discount_basis,
                point_rate=max([value for value in [api_point_rate, text_point_rate] if value is not None], default=None),
                coupon_available=coupon_available,
                coupon_discount_rate=coupon_rate,
                coupon_amount=coupon_amount,
                coupon_text=coupon_text,
                affiliate_rate=as_float(item.get("affiliateRate")),
                review_count=as_int(item.get("reviewCount")),
                review_average=as_float(item.get("reviewAverage")),
                shipping="free/included" if as_int(item.get("postageFlag")) == 0 else None,
                sale_start=item.get("startTime"),
                sale_end=item.get("endTime"),
                image_url=first_image_url(item.get("mediumImageUrls")),
            )
            results.append(score_item(deal, min_discount_rate, min_point_rate))

        if len(items) < hits:
            break
        page += 1
        if pause:
            time.sleep(pause)

    return results[:limit]


def search_yahoo(
    keyword: str,
    limit: int,
    min_price: int | None,
    max_price: int | None,
    sort: str,
    user_rank: str,
    discounted_only: bool,
    premium_points: bool,
    min_discount_rate: float,
    min_point_rate: float,
    pause: float,
) -> list[DealItem]:
    app_id = os.getenv("YAHOO_APP_ID")
    if not app_id:
        print("skip Yahoo Shopping: set YAHOO_APP_ID", file=sys.stderr)
        return []

    affiliate_id = os.getenv("YAHOO_AFFILIATE_ID") or None
    affiliate_type = os.getenv("YAHOO_AFFILIATE_TYPE") or ("vc" if affiliate_id else None)
    results: list[DealItem] = []
    start = 1

    while len(results) < limit and start <= 1000:
        batch_size = min(50, limit - len(results), 1001 - start)
        params = {
            "appid": app_id,
            "affiliate_type": affiliate_type,
            "affiliate_id": affiliate_id,
            "query": keyword,
            "results": batch_size,
            "start": start,
            "in_stock": "true",
            "price_from": min_price,
            "price_to": max_price,
            "sort": sort,
            "user_rank": user_rank,
            "image_size": 300,
            "is_discounted": "true" if discounted_only else None,
        }
        payload = get_json(YAHOO_ENDPOINT, params)
        hits = payload.get("hits") or []
        if not hits:
            break

        for hit in hits:
            discount_rate, discount_basis = best_yahoo_discount(hit)
            point_rate, point_amount = yahoo_point_rate_and_amount(hit, premium=premium_points)
            coupon_available, coupon_rate, coupon_amount, coupon_text = coupon_from_text(
                hit.get("headLine"),
                hit.get("name"),
                hit.get("description"),
            )
            shipping = hit.get("shipping") or {}
            seller = hit.get("seller") or {}
            review = hit.get("review") or {}
            price_label = hit.get("priceLabel") or {}
            deal = DealItem(
                source="yahoo",
                name=str(hit.get("name") or ""),
                price=as_int(hit.get("price")),
                url=str(hit.get("url") or ""),
                shop=seller.get("name"),
                code=hit.get("code"),
                discount_rate=discount_rate,
                discount_basis=discount_basis,
                point_rate=point_rate,
                point_amount=point_amount,
                coupon_available=coupon_available,
                coupon_discount_rate=coupon_rate,
                coupon_amount=coupon_amount,
                coupon_text=coupon_text,
                affiliate_rate=as_float(hit.get("affiliateRate")),
                review_count=as_int(review.get("count")),
                review_average=as_float(review.get("rate")),
                shipping=shipping.get("name"),
                sale_start=str(price_label.get("periodStart")) if price_label.get("periodStart") else None,
                sale_end=str(price_label.get("periodEnd")) if price_label.get("periodEnd") else None,
                image_url=first_image_url(hit.get("exImage")) or first_image_url(hit.get("image")),
            )
            results.append(score_item(deal, min_discount_rate, min_point_rate))

        if len(hits) < batch_size:
            break
        start += batch_size
        if pause:
            time.sleep(pause)

    return results[:limit]


def is_deal(item: DealItem, min_discount_rate: float, min_point_rate: float) -> bool:
    return (
        (item.discount_rate or 0.0) >= min_discount_rate
        or (item.point_rate or 0.0) >= min_point_rate
        or item.coupon_available
        or bool(item.sale_start or item.sale_end)
    )


def yen(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:g}"


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def print_table(items: Iterable[DealItem]) -> None:
    rows = list(items)
    if not rows:
        print("No matching items.")
        return

    header = f"{'#':>2} {'src':<7} {'score':>5} {'price':>9} {'off%':>6} {'pt':>5} {'cpn':>3}  {'name'}"
    print(header)
    print("-" * len(header))
    for index, item in enumerate(rows, 1):
        print(
            f"{index:>2} {item.source:<7} {item.score:>5g} {yen(item.price):>9} "
            f"{pct(item.discount_rate):>6} {pct(item.point_rate):>5} {'yes' if item.coupon_available else '-':>3}  {truncate(item.name, 64)}"
        )
        if item.reason:
            print(f"   reason: {item.reason}")
        if item.shop:
            print(f"   shop: {item.shop}")
        if item.coupon_text:
            print(f"   coupon: {item.coupon_text}")
        if item.url:
            print(f"   url: {item.url}")


def write_json(path: Path, items: list[DealItem]) -> None:
    path.write_text(
        json.dumps([asdict(item) | {"effective_price": item.effective_price} for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(path: Path, items: list[DealItem]) -> None:
    fields = [
        "source",
        "score",
        "name",
        "price",
        "effective_price",
        "discount_rate",
        "discount_basis",
        "point_rate",
        "point_amount",
        "coupon_available",
        "coupon_discount_rate",
        "coupon_amount",
        "coupon_text",
        "affiliate_rate",
        "shop",
        "code",
        "review_count",
        "review_average",
        "shipping",
        "sale_start",
        "sale_end",
        "url",
        "image_url",
        "reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            row = asdict(item)
            row["effective_price"] = item.effective_price
            writer.writerow({field: row.get(field) for field in fields})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find Rakuten/Yahoo Shopping items with large discounts or high points.")
    parser.add_argument("keyword", help="Search keyword, for example: 'anker 充電器'")
    parser.add_argument("--source", choices=["all", "rakuten", "yahoo"], default="all")
    parser.add_argument("--limit", type=int, default=30, help="Items to fetch per enabled source.")
    parser.add_argument("--top", type=int, default=20, help="Items to show after scoring.")
    parser.add_argument("--min-price", type=int)
    parser.add_argument("--max-price", type=int)
    parser.add_argument("--min-discount-rate", type=float, default=20.0, help="Discount threshold in percent.")
    parser.add_argument("--min-point-rate", type=float, default=5.0, help="Point multiplier/approx percent threshold.")
    parser.add_argument("--show-all", action="store_true", help="Show scored items even if below thresholds.")
    parser.add_argument("--output", type=Path, help="Write JSON or CSV based on extension.")
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Optional env file path.")
    parser.add_argument("--pause", type=float, default=0.2, help="Pause seconds between paginated API calls.")
    parser.add_argument("--rakuten-sort", default="standard")
    parser.add_argument("--yahoo-sort", default="-score")
    parser.add_argument("--yahoo-user-rank", choices=["guest", "bronze", "silver", "gold", "platinum", "diamond"], default="guest")
    parser.add_argument("--yahoo-discounted-only", action="store_true")
    parser.add_argument("--yahoo-premium-points", action="store_true", help="Use Yahoo premium point fields when ranking.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    load_env_file(args.env_file)

    if args.limit < 1 or args.top < 1:
        print("--limit and --top must be positive", file=sys.stderr)
        return 2

    items: list[DealItem] = []
    try:
        if args.source in {"all", "rakuten"}:
            items.extend(
                search_rakuten(
                    keyword=args.keyword,
                    limit=args.limit,
                    min_price=args.min_price,
                    max_price=args.max_price,
                    sort=args.rakuten_sort,
                    min_discount_rate=args.min_discount_rate,
                    min_point_rate=args.min_point_rate,
                    pause=args.pause,
                )
            )
        if args.source in {"all", "yahoo"}:
            items.extend(
                search_yahoo(
                    keyword=args.keyword,
                    limit=args.limit,
                    min_price=args.min_price,
                    max_price=args.max_price,
                    sort=args.yahoo_sort,
                    user_rank=args.yahoo_user_rank,
                    discounted_only=args.yahoo_discounted_only,
                    premium_points=args.yahoo_premium_points,
                    min_discount_rate=args.min_discount_rate,
                    min_point_rate=args.min_point_rate,
                    pause=args.pause,
                )
            )
    except ApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1

    if not args.show_all:
        items = [item for item in items if is_deal(item, args.min_discount_rate, args.min_point_rate)]
    items.sort(key=lambda item: (item.score, item.review_count or 0), reverse=True)
    items = items[: args.top]

    if args.output:
        suffix = args.output.suffix.lower()
        if suffix == ".json":
            write_json(args.output, items)
        elif suffix == ".csv":
            write_csv(args.output, items)
        else:
            print("--output must end with .json or .csv", file=sys.stderr)
            return 2

    if args.format == "json":
        print(json.dumps([asdict(item) | {"effective_price": item.effective_price} for item in items], ensure_ascii=False, indent=2))
    elif args.format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(["source", "score", "price", "discount_rate", "point_rate", "name", "url"])
        for item in items:
            writer.writerow([item.source, item.score, item.price, item.discount_rate, item.point_rate, item.name, item.url])
    else:
        print_table(items)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
