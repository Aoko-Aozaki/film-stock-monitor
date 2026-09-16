---
name: film-stock-monitor
description: 查询或监控美国线上商家的 Fujifilm Provia 100F、Velvia 50 胶卷（135/120）库存、价格和补货变化。用于跑一轮巡检、比较前后快照、解读 IN_STOCK/BLOCKED 等状态，或维护商家注册表和失效的商品链接。
---

# 胶卷库存监控

在本目录运行 `scripts/check_stock.sh`。默认只查不需要浏览器的商家，`--all` 加上 Headless 和 Dakis 两层。`--json runs/<名称>.jsonl` 保存快照，下一轮用 `--diff runs/<上一轮>.jsonl` 比较。参数、退出码和安装见 [README.md](README.md)。

## 解读结果

- 先报 `IN_STOCK`，再报 `BACKORDER` 和 `RESTOCK_DATED`。有到货日不是现货。
- `BLOCKED` 是请求或浏览器失败，`UNKNOWN` 是没有可靠的库存信号。两者都不是缺货，要点名受影响的商家和 SKU。
- 只对本轮实际检查过的商家下结论。默认运行不含 Headless/Dakis，注册表也不是美国全部在线商店。
- `--diff` 中上一轮为 `BLOCKED`/`UNKNOWN`、本轮出现可买线索的，只能叫“新发现”，不能叫补货。

## 维护采集源

商家和商品在 [stores.json](stores.json)；逐店端点、平台限制和最近一次实测在 [reference.md](reference.md)。

- Shopify 的 `available=true` 可能只是允许超卖，要看变体的 `inventory_quantity` 和 `inventory_policy`。Handle 404 时先跑 `--discover <host>` 找改名后的商品。
- 页面 JSON-LD 只在该商家标了 `jsonld_trusted: true` 时才能作兜底。状态文案可能来自推荐商品区，发有货告警前先打开链接核对。
- Dakis 店的空模板可能是 Avina 脚本被拦；`carries: []` 表示未验证，不是未上架。
- Dwayne's API 只有价格没有库存。名称含 `EXP` 的是过期片，不算新片补货。
