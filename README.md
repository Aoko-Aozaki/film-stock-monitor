# Provia 100F / Velvia 50 库存监控

富士的这两款反转片这两年一直缺货，补货窗口往往只有几个小时。这个小工具把美国线上卖胶卷的商家登记在 `stores.json` 里，每轮跑一遍，把 135 和 120 规格的库存状态、价格整理成一张表，还能和上一轮的快照比较，看有没有从缺货变成可买的。

## 安装

需要 Python 3.10 以上。在项目目录里：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # 只跑默认巡检的话可以不装
```

`scripts/check_stock.sh` 会自己找解释器：先看环境变量 `FILM_PYTHON`，再看项目里的 `.venv`，最后退回当前环境的 `python3`。用 Conda 的话可以 `conda env create -f environment.yml`，然后用 `FILM_PYTHON` 指向那个环境的 python，或者直接用 `scripts/check_stock_base.sh`（默认激活 base，`FILM_CONDA_ENV` 可以改）。

浏览器只有 `--all`、`--method headless`、`--method dakis`，或者用 `--store` 点名这几类商家时才会用到。没装 Playwright 时这些商家会记成 `BLOCKED`，其他商家不受影响。

## 日常怎么用

```bash
scripts/check_stock.sh                        # 默认：Shopify、直连 HTML、Dwayne's API，20 家左右，几分钟
scripts/check_stock.sh --all                  # 再加 4 家 Headless 和 15 家 Dakis，可能要几十分钟
scripts/check_stock.sh --store freestyle,keh  # 只查指定商家，id 见 stores.json
scripts/check_stock.sh --method shopify       # 只查一种采集方式
scripts/check_stock.sh --discover essentialphotosupply.com   # 某个 Shopify 站上搜 Provia/Velvia 商品
```

想知道"有没有变化"，就每轮存快照，下一轮拿上一轮的来比：

```bash
scripts/check_stock.sh --json runs/2026-09-16.jsonl
scripts/check_stock.sh --json runs/2026-09-17.jsonl --diff runs/2026-09-16.jsonl
```

比较时前后两轮最好用同样的筛选条件，否则上一轮有、这一轮没抓的商家会被单独列出来提醒你。`runs/` 目录不进 Git。

几个环境变量：

| 变量 | 作用 |
|---|---|
| `FILM_PYTHON` | 指定解释器 |
| `FILM_SHOPIFY_INTERVAL` | Shopify 请求间隔秒数，默认 4。Shopify 的限流是按 IP 封整个店铺前台的，别调太小 |
| `FILM_BROWSER_CHANNEL` | Playwright 用哪个浏览器 channel，比如 `chrome`。默认先试本机 Chrome，不行再用 Playwright 自带的 Chromium |
| `FILM_CONDA_ENV` | 只对 `check_stock_base.sh` 有效，Conda 环境名 |

## 怎么看结果

表格按状态排序，可买的排最前面。状态含义：

| 状态 | 含义 |
|---|---|
| `IN_STOCK` | 有现货 |
| `BACKORDER` | 没现货，但商家接受下单排队 |
| `RESTOCK_DATED` | 商家给了到货日期，现在没货。目前只有 Freestyle 会公布 |
| `OUT_OF_STOCK` | 缺货 |
| `IN_STORE_ONLY` | 只在门店卖，或要打电话问 |
| `DELISTED` | 商品页明确说下架了，或者返回 404。站点改版也会让老链接 404 |
| `BLOCKED` | 请求被拦、浏览器失败，没查到 |
| `UNKNOWN` | 页面拿到了，但没找到可信的库存信号 |

两点要记住：

- `BLOCKED` 和 `UNKNOWN` 不是缺货，只是没查到。`--diff` 也不会把它们当作库存变化；上一轮没查到、这一轮可买的，会单独列成"新发现"而不是"补货"。
- 页面上的状态文案和 JSON-LD 有时来自"相关商品"区，不是主商品。收到有货告警先打开链接看一眼。

退出码是给定时任务用的：

| 退出码 | 含义 |
|---|---|
| `0` | 没有新的补货或到货线索。注意没传 `--diff` 时，就算表里有现货也返回 0 |
| `10` | `--diff` 发现从缺货变成可买或有到货日，或者出现了新的可买商品 |
| `20` | 没有可买线索，且至少有一项是 `BLOCKED` 或 `UNKNOWN`，不能说全缺货。`--discover` 请求全失败时也返回 20 |
| `2` | 参数不对、商家 id 不存在，或上一轮快照读不了 |

退出码只是个概括，同一轮里可能既有可买线索又有没查到的商家，具体还得看表。

## 覆盖了哪些商家

| 采集方式 | 商家数 | 默认跑 | 说明 |
|---|---:|---|---|
| Shopify 商品 JSON | 10 | 是 | 读 `.js` 接口的价格、变体和库存字段，最稳 |
| 直连 HTML | 9 | 是 | 解析页面文案。有几家会 403，具体哪几家看出口 IP，Samy's 在两个网络下都被拦 |
| Dwayne's API | 1 | 是 | 只有价格没有库存，固定记 `UNKNOWN` |
| Headless 浏览器 | 4 | 否 | B&H、Adorama、Unique Photo、KEH。碰到 Cloudflare 1015 限流页会冷却后重试一次 |
| Dakis / Avina | 15 | 否 | 独立相机店常用的电商平台。其中 11 家还没验证到底上架了哪些 SKU |
| 占位 | 1 | 是 | Roberts Camera，Cloudflare 一直过不去，固定输出 `BLOCKED` |

默认一轮跑 21 家的记录，其中 Roberts 是占位。登记了不等于能读到库存：能不能访问和出口 IP 关系很大，同一家店换个网络结论就可能翻转，`reference.md` 里记的是最近一次实测（2026-09-16）。

Amazon、eBay、Walmart 这类平台故意没放进来。上面挂的大多是第三方卖家的过期片、加价倒卖或日本直邮，和"美国零售商补货"不是一回事。要加商家的话，先确认它是美国渠道、卖的是目标规格、不是过期片，页面上能读到可信的库存信号，再写进 `stores.json`。

## 已知的坑

- Shopify 的 `available=true` 不代表有现货。有的店允许超卖，库存为 0 也能加购。脚本会看 `inventory_quantity` 和 `inventory_policy`，超卖挂单记为 `BACKORDER`。
- 状态判定是"按严重度优先"：页面任何地方出现缺货文案，都会盖掉主商品的有货。这样不会误报有货，但如果"相关商品"区有一条 Out of stock，主商品真补货了也会被漏掉。正确做法是先把文本裁到主商品容器再判定，还没做。
- Dakis 平台对任何商品 UUID 都返回 200。商品内容靠 `avina.mydakis.com` 的脚本注入，那个域名被拦时页面就是个空壳，和"没上架"长得一模一样。脚本对空模板一律记 `UNKNOWN`。
- Dwayne's 的 Velvia 50 120 五卷装是过期片（名字里带 EXP），价格不能和新片放一起比。

更多逐店细节（端点、文案锚点、限流、踩过的坑）都在 `reference.md`。

## 文件

| 文件 | 用途 |
|---|---|
| `SKILL.md` | 给 Codex / Claude Code 这类 agent 用的操作要点 |
| `reference.md` | 逐店端点、已知限制和实测记录 |
| `stores.json` | 商家、商品和采集方式的注册表 |
| `scripts/check_stock.py` | 采集、状态归一化、快照比较 |
| `scripts/check_stock.sh` | 启动器，负责找 Python |
| `scripts/check_stock_base.sh` | Conda 用户的启动器 |
| `runs/` | 本地快照，不入库 |
