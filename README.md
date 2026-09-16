# 胶卷库存监控Skill

监控 **Fujifilm Provia 100F / Velvia 50**（135 与 120 规格）在美国 40 家零售商的
库存与价格，并与上一轮快照比对。这两款反转片在美国每年只补货 2–3 次，到货后数小时至数天售罄。

## 快速开始

```bash
git clone https://github.com/Aoko-Aozaki/film-stock-monitor.git
cd film-stock-monitor

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # 仅 --all / headless 需要

scripts/check_stock.sh                            # 常规巡检，1–3 分钟
```

启动器会自动使用项目里的 `.venv`，不需要手动激活。

## 用法

```bash
# 存档 + 与上一轮比对（推荐的日常用法）
scripts/check_stock.sh --json runs/snap-$(date +%F).jsonl \
                       --diff  runs/snap-上一轮.jsonl

scripts/check_stock.sh --all                 # 含 headless 与 Dakis 平台，+5–15 分钟
scripts/check_stock.sh --store freestyle,keh # 只查指定商家
scripts/check_stock.sh --method shopify      # 只查某一类平台
scripts/check_stock.sh --discover <host>     # 对某 Shopify 站做 SKU 发现
```

退出码：`0` 无事发生 ｜ `10` 有补货 ｜ `20` 全被拦截、数据不可信。可直接挂定时任务。

## 输出

```
状态              商家                      SKU             价格  备注
BACKORDER       Pro Camera Hawaii       P135        $28.99  无货 qty=0 policy=continue
RESTOCK_DATED   Freestyle Photographic  P135        $35.95  到货 Oct 30, 2026
OUT_OF_STOCK    KEH Camera              P135             —
BLOCKED         Samy's Camera           P135             —  HTTP 403
```

状态分七类，**区分它们是这个项目的核心**：

| 状态 | 含义 |
|---|---|
| `IN_STOCK` | 真有现货 |
| `BACKORDER` | 无现货但可下单排队 |
| `RESTOCK_DATED` | 商家给了到货日 |
| `OUT_OF_STOCK` | 纯缺货 |
| `IN_STORE_ONLY` | 线上不卖，仅到店 |
| `DELISTED` | 商品页已下架 |
| `BLOCKED` | **抓取失败——不是没货** |

`BLOCKED` 绝不能当成「没货」。把抓不到误报成没货，是这类系统最容易犯也最严重的错误。

## 覆盖范围

40 家商家，6 种采集路径：

| 路径 | 家数 | 说明 |
|---|---|---|
| Shopify | 10 | `<商品URL>.js` 一次拿到库存、价格、富士料号与 JAN |
| 直连 HTML | 9 | HTTP + 库存文案正则 |
| Dakis / Avina 平台 | 15 | 必须渲染；全平台共用同一套商品 UUID |
| Headless | 4 | B&H / Adorama / Unique Photo / KEH |
| 私有 API | 1 | Dwayne's Photo（无库存字段） |

商家注册表在 `stores.json`，**新增商家只需加一条记录，不用改代码**。


## 项目结构

```
SKILL.md          Claude Code skill 指令：何时采集、如何解读
reference.md      逐店访问方法、实测文案、已知陷阱
stores.json       商家与 SKU 注册表（改这里就能增删商家）
scripts/
  check_stock.py  采集器
  check_stock.sh  启动器（自动探测 .venv）
  check_stock_base.sh  Conda 入口
runs/             巡检产物（已 gitignore）
```

## 免责

仅抓取各商家公开商品页，用于个人补货提醒。请遵守目标站点的服务条款，不要调高抓取频率。
