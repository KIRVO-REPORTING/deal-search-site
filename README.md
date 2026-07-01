# 电商搜索 Deal Search

用乐天市场和 Yahoo Shopping API 搜索商品，并把“折扣明显”或“ポイント倍率高”的商品排到前面。

## 准备 API Key

1. 复制环境变量样例：

   ```bash
   cp .env.example .env
   ```

2. 填入 key：

   ```bash
   RAKUTEN_APPLICATION_ID=你的 Rakuten App ID
   RAKUTEN_ACCESS_KEY=你的 Rakuten Access Key
   RAKUTEN_REFERER=https://你注册时填写的域名/
   YAHOO_APP_ID=你的 Yahoo Client ID
   ```

   `RAKUTEN_AFFILIATE_ID`、`YAHOO_AFFILIATE_ID` 可选。`RAKUTEN_REFERER` 用来匹配 Rakuten app 里登记的允许网站；如果遇到 `HTTP_REFERRER_NOT_ALLOWED`，先确认这里的域名和 Rakuten 后台一致。

官方文档：

- Rakuten Ichiba Item Search API: https://webservice.rakuten.co.jp/documentation/ichiba-item-search
- Yahoo Shopping itemSearch v3: https://developer.yahoo.co.jp/webapi/shopping/v3/itemsearch.html

## 使用

```bash
python3 search_deals.py "anker 充電器"
```

只查 Yahoo，筛 30% 以上折扣或 10 倍以上ポイント：

```bash
python3 search_deals.py "炊飯器" --source yahoo --min-discount-rate 30 --min-point-rate 10
```

导出 CSV：

```bash
python3 search_deals.py "洗剤" --top 50 --output deals.csv
```

导出 JSON：

```bash
python3 search_deals.py "イヤホン" --format json --output deals.json
```

## 判定逻辑

- Yahoo Shopping: 使用 `priceLabel.defaultPrice`、`priceLabel.fixedPrice`、`priceLabel.discountedPrice`、`premiumDiscountRate` 计算折扣率；ポイント优先读取 2025 年后推荐的 `lyLimitedBonusTimes` / `lyLimitedBonusAmount` 字段。
- Rakuten: 官方商品搜索结果有 `pointRate`、`startTime`、`endTime`，但没有稳定的“原价 vs 现价”字段；因此折扣会通过标题、catchcopy、caption 中的 `50%OFF`、`半額`、`30%OFF` 等文案做启发式识别，ポイント也会额外识别 `P10倍`、`ポイント10倍` 这类促销文案。
- 默认只显示满足任一条件的商品：折扣率 >= `--min-discount-rate`、ポイント >= `--min-point-rate`、或 API 返回限时 sale 字段。加 `--show-all` 可以看全部打分结果。

## 常用参数

- `--source all|rakuten|yahoo`
- `--limit`: 每个平台抓取多少条，默认 30
- `--top`: 最终显示多少条，默认 20
- `--min-price` / `--max-price`
- `--min-discount-rate`: 默认 20
- `--min-point-rate`: 默认 5
- `--yahoo-discounted-only`: Yahoo 侧只请求 sale 对象
- `--yahoo-user-rank guest|bronze|silver|gold|platinum|diamond`
- `--yahoo-premium-points`: 用 Yahoo premium ポイント字段参与评分
