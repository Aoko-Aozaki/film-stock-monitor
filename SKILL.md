---
name: film-stock-monitor
description: 采集美国市场 Fujifilm Provia 100F / Velvia 50（135 与 120 规格）在各零售商的实时库存与价格，并与上一轮快照比对找出补货跳变。当用户询问这两款胶卷"有没有货""什么价""哪里能买""补货了吗"，或要求刷新/复核库存清单、新增监控商家、排查某商家抓不到数据时使用。
---

# 胶卷库存采集（Provia 100F / Velvia 50）

## 这个 skill 解决什么

这两款胶卷在美国每年只补货 2–3 次，到货后数小时至数天售罄。所以任务从来不是比价，
而是**尽早发现「缺货 → 有货」的跳变**。40 家商家分布在 6 种电商平台上，反爬强度差异
极大，本 skill 把踩过的坑固化成了可直接运行的采集器。

**跳变才是信号，绝对状态是背景。** 单看一轮结果只能知道"现在没货"，这几乎永远为真、
没有信息量。所以每轮都应存档并与上一轮比对——`--diff` 就是干这个的。

## 快速开始

```bash
# 常规巡检：shopify + html + api 三类，约 20 家，1–3 分钟
scripts/check_stock.sh

# 存档 + 与上一轮比对（推荐的日常用法）
scripts/check_stock.sh --json snap-$(date +%F).jsonl --diff snap-上一轮.jsonl

# 全量（额外含 headless 与 Dakis 平台，+5–15 分钟）
scripts/check_stock.sh --all

# 只查某几家 / 某一类
scripts/check_stock.sh --store freestyle,keh
scripts/check_stock.sh --method shopify

# 某个 Shopify 站的 SKU 发现（商家改 handle 后用它找回来）
scripts/check_stock.sh --discover procamerahawaii.com
```

退出码：`0` 无事发生 ｜ `10` 有补货跳变 ｜ `20` 全被拦截、数据不可信。可直接用于定时任务。

### 环境

**仓库自带 `.venv`（Python 3.12 + playwright），无需激活任何环境**，
`scripts/check_stock.sh` 会自动用它。重建方法：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

解释器优先级为 `FILM_PYTHON` > 项目 `.venv` > 当前环境的 `python`，所以想临时换
解释器直接 `FILM_PYTHON=/path/to/python scripts/check_stock.sh`。也可以改用 Conda：
`conda env create -f environment.yml && conda activate film-stock-monitor`，
或用 `scripts/check_stock_base.sh`（动态找 Conda，默认 `base`，`FILM_CONDA_ENV` 可改）。

Headless 优先用本机 Chrome，找不到才回退 playwright 自带的 Chromium，可用
`FILM_BROWSER_CHANNEL` 指定 channel。**注意两者版本要对得上**：升级 playwright 后
自带 Chromium 的 build 号会变，不重跑 `playwright install chromium` 就只剩本机
Chrome 这一条路，一旦 Chrome 也不可用，headless 与 Dakis 两层会整层变成 BLOCKED。

## 汇报结论时必须遵守的三条

**1. 先回答有没有货，再展开细节。** 用户问的是能不能买到。

**2. 区分五种"没货"——它们的价值完全不同，不要笼统说"缺货"：**

| 状态 | 含义 | 用户可以做什么 |
|---|---|---|
| `IN_STOCK` | 真有现货 | 立即下单 |
| `BACKORDER` | 无现货但可下单排队 | 现在就能付款排队 |
| `RESTOCK_DATED` | 商家给了到货日 | 记下日期，到点蹲守 |
| `OUT_OF_STOCK` | 纯缺货 | 只能订阅通知 |
| `IN_STORE_ONLY` | 线上根本不卖 | 得打电话或到店 |

**3. `BLOCKED` 绝不能当作"没货"汇报。** 那是抓取失败。必须明确说明"这几家没查到"并
列出是哪几家。把抓不到误报成没货，是这个系统最容易犯也最严重的错误。当前一轮里
`BLOCKED` 常占三分之一，绝不是可以忽略的少数。

## 可达性完全取决于出口 IP

**这是本项目最反直觉、也最浪费时间的一点。** 同一套代码在两个网络下结果天差地别，
换网络后必须重跑全量再下结论，否则会把"这个网络连不上"写成"这家店没货"。

2026-09-16 在当前出口 IP 实测：

| 商家 | 实测结果 |
|---|---|
| MPEX | 403，Sucuri WAF 拦截页 |
| Samy's | 403，JBossWeb 错误页 |
| Fotocare / Dodd | 403，Cloudflare `Attention Required!` |
| B&H / Adorama | headless 也 403（B&H 卡在 `Just a moment...`，Adorama 返回空 body） |
| `avina.mydakis.com` | 全站 403 → **整个 Dakis 层 15 家全部读不到** |
| KEH | **可读**（见下） |

换真实浏览器 UA、`sec-ch-ua`、`Sec-Fetch-*` 全套头都无效——拦的是 TLS 指纹与 IP，
不是请求头。**不要再在头部上浪费时间**，直接换网络或改用 headless。

## 六个已知陷阱

1. **Shopify 的 `available` 不等于有现货。** 卖家设 `inventory_policy="continue"`
   （允许超卖）时，`inventory_quantity=0` 也照样返回 `available=true`。Pro Camera
   Hawaii 4 个 SKU 全是这种挂单。判定必须看 `inventory_quantity` 与
   `inventory_policy`，只有确有库存数才算 `IN_STOCK`，超卖挂单归 `BACKORDER`。

2. **JSON-LD 的 `availability` 会撒谎。** Unique Photo 写 `InStock`，页面实际是
   `Backordered - On Allocation`。`stores.json` 里每家都标了 `jsonld_trusted`，
   **只信标 true 的**，其余一律以页面可见文案为准。

3. **缺货页上到处是"in stock"。** KEH 缺货原文是
   `this item is temporarily unavailable` + `notified when it comes back in stock`，
   朴素地匹配 `in stock` 会直接误报有货。缺货规则必须优先于有货规则，且
   `in stock` 要排除 `back in stock`。

4. **Shopify 的 429 限流跨店按 IP 生效，且封整个店铺前台**（不只 `.js`，普通商品页
   一样 429），10 家会同时不可用，恢复需相当长时间。脚本内置全局限速器（默认每 4 秒
   一次，`FILM_SHOPIFY_INTERVAL` 可调）+ 带抖动的指数退避。**不要为提速改回并发**，
   一旦触发代价是整层数据全丢，且重试无用，只能等冷却。

5. **商家会悄悄改 handle，旧 URL 变 404。** Essential Photo Supply 4 个 handle 在
   2026-09-16 全部改名，旧的全 404。看到 `DELISTED` 先用 `--discover` 找回来，
   **确认站内真的搜不到了再从 `stores.json` 移除**。

6. **过期片必须单独打标，不可与新片比价。** BuyMoreFilm 挂着十余个 2000–2012 年
   的过期片（JAN 常为 `4902520191519`），Dwayne's 的 V120×5 商品名带 `EXP`。
   这些价格显著低于新片，混在一起会让价格告警失真。

## 各平台访问方法

完整的逐店端点、库存文案锚点、以及每家 JSON-LD 是否可信，见 `reference.md`。要点：

- **Shopify（10 家）** — `<商品URL>.js` 一次拿到库存、价格、富士料号（`sku`）与
  JAN（`barcode`），最快最稳
- **Dwayne's** — 私有 API `/v1/products/<id>.json`，但**无库存字段**
- **直连 HTML（9 家）** — HTTP + 正则匹配库存文案，其中 4 家当前被 WAF 拦
- **Headless（4 家）** — B&H / Adorama / Unique Photo / KEH
- **Dakis 平台（15 家）** — 必须渲染；平台对任何 UUID 都返回 200，须用三态判别

## 维护

- **新增商家**：往 `stores.json` 的 `stores[]` 加一条，按 `method` 选平台类型即可，
  无需改代码。
- **发现新 SKU**：Shopify 站用 `--discover <host>`；其他站看 `reference.md` 里各自的
  站内搜索端点。注意 `suggest.json` 的 `limit` 上限是 10，**命中 10 条时必须换更具体
  的关键词再查一遍**——当前多数店都会撞到这个上限，覆盖并不完整。
- **跨店去重**：用富士料号 + JAN 双键。同一规格存在多个 JAN（Provia 135 有 `…626`
  和 `…633`），单键会漏；部分店的 `barcode` 填的是美国 UPC（如 `074101574647`）
  甚至内部编号，也要容错。**绝不要靠标题匹配**——Velvia 50 / 100 / 100F /
  Provia 100F 在多家店里混排，标题匹配必然串味。
- **Dakis 店的 `carries: []` 表示未验证，不表示未上架。** 脚本对这种店会把全部已知
  商品 UUID 都探一遍。验证通过后再把实际结果写回 `carries`。
