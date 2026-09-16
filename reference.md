# 逐店访问方法参考

> 最近一次全面实测：2026-09-16。改代码前先看这里，能省掉大量试错。
>
> ⚠️ **本文件里的"可达/不可达"全部与出口 IP 绑定。** 换网络后结论可能整块翻转，
> 必须重跑全量再更新本文件，不要把一次网络故障固化成"这家店没货"或"此站无解"。

## 通用请求头

```
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36
Accept: text/html,application/xhtml+xml,*/*;q=0.8
Accept-Language: en-US,en;q=0.9
Accept-Encoding: identity
```

`Accept-Encoding: identity` **不能省** —— 部分站点返回压缩内容会导致解码乱码。

**请求头对 WAF 无效。** 2026-09-16 对 MPEX / Samy's / Fotocare / Dodd 补齐了
`sec-ch-ua`、`sec-ch-ua-platform`、`Sec-Fetch-Dest/Mode/Site/User`、
`Upgrade-Insecure-Requests` 并改用 gzip，四家仍然全部 403，与精简头完全一致。
拦截发生在 TLS 指纹 / IP 信誉层，**不要再在头部上花时间**。

---

## Shopify（10 家）—— JSON 接口直取，最稳

Essential Photo Supply、Pro Camera Hawaii、BuyMoreFilm、Reformed Film Lab、
Film Supply Club、pictureline、K&M Camera、Pro Photo Supply、Camera West、
B&C Camera（`store.bandccamera.com`）

```bash
# ① 单品库存 + 价格 —— 日常巡检用这个
curl -s "https://<域名>/products/<handle>.js"

# ② 发现新 SKU
curl -s "https://<域名>/search/suggest.json?q=velvia&resources[type]=product&resources[limit]=10"

# ③ 全目录 —— 慎用，最容易触发限流
curl -s "https://<域名>/products.json?limit=250&page=1"
```

### ⚠️ `available` 不等于有现货

`.js` 的 `available` 只表示"能不能加入购物车"。关键字段在 variant 里：

| 字段 | 含义 |
|---|---|
| `inventory_quantity` | 真实库存数 |
| `inventory_policy` | `deny`=售罄即下架；`continue`=**允许超卖** |
| `inventory_management` | 为空表示该店根本没启用库存管理，数量字段无意义 |

Pro Camera Hawaii 实测：4 个 SKU 全部 `available=true` + `inventory_quantity=0`
+ `inventory_policy=continue`，即允许超卖的挂单，**并无现货**。

判定：`continue` 且 `qty<=0` → `BACKORDER`；启用了管理且 `qty<=0` → `OUT_OF_STOCK`；
确有库存数 → `IN_STOCK`；未启用库存管理则只能退回 `available`。

### 顺带白拿的两个主键

`.js` 的 variant 里 `sku` 常是富士料号、`barcode` 常是 JAN，是跨店去重最省事的来源，
比从页面上抠 UPC/MPN 可靠得多。但**有脏值**：Film Supply Club 的 barcode 是内部编号
（`82854229`）、pictureline 部分是美国 UPC（`074101574647`）、Reformed Film Lab 的
`sku` 整体为空。

### 限流（重要）

Shopify 的 429 **跨店按 IP 生效，且封整个店铺前台** —— 触发后连普通商品页都 429，
10 家同时不可用，恢复需相当长时间。并发拉 `products.json` 必触发。脚本内置全局
限速器：所有 Shopify 请求串行、间隔默认 4 秒（`FILM_SHOPIFY_INTERVAL` 可调），
配合带抖动的指数退避（5→10→20→40→60 秒）。**宁可慢，不要把 IP 打进小黑屋。**

### suggest.json 的覆盖度不足

`limit` 上限是 10。2026-09-16 实测，`velvia` / `provia` / `fujichrome` 三个词在
BuyMoreFilm、pictureline、K&M、Pro Camera Hawaii 等多家**都撞到了 10 条上限**，
说明发现结果并不完整，必须换更具体的词（加规格、加 `RVP`/`RDP`、加 `120`）复查。

搜索结果还会混进噪声：BuyMoreFilm 有个 `Keychain - Fujichrome Provia 100`
（钥匙扣）也会命中。入库前要按规格词过滤掉 4x5 / 8x10 页片与 Velvia 100。

### Handle 会变

Essential Photo Supply 4 个 handle 在 2026-09-16 全部改名，旧 handle 一律 404。
看到整店 `DELISTED` 先跑 `--discover`，不要直接删。改名后的新 handle 已写回
`stores.json`。

---

## Dwayne's Photo —— 私有 API

```bash
curl -s "https://www.dwaynesphoto.com/v1/products/52520.json"                # 名称 + 价格
curl -s "https://www.dwaynesphoto.com/v1/products/52520/price_forecast.json" # 仅价格
```

商品 ID：`52512`=Provia 135 ｜ `52520`=Provia 120 ｜ `54541`=Velvia 50 120×5（**EXP 过期片**）

⚠️ 该 API **无库存字段**。脚本将其记为 `UNKNOWN`，价格可参考，但能否下单需核实商品页。
`54541` 的名称带 `EXP`，属于过期片，不能当作新片补货。

---

## 直连 HTTP + HTML 解析（9 家）

| 商家 | 2026-09-16 | 库存文案锚点 | 站内搜索 | JSON-LD |
|---|---|---|---|---|
| **Freestyle** | ✅ 可达 | `Currently out of stock. Due: <日期>` ／ `Currently Unavailable` | `/search?q={q}` ✅ | ❌ 不可信 |
| **Hunt's** | ✅ 可达 | `Out of Stock` + `NOTIFY WHEN AVAILABLE` | 需 headless 提交首页搜索框 | ❌ |
| **Blue Moon** | ✅ 可达 | `Availability: 0` + `Out of Stock` | 需 headless | ❌ |
| **Ace Photo** | ✅ 可达 | `Current Stock:` + `Out of stock` | 需 headless → `/search-results-page?q={q}` | ✅ 可信 |
| **Ultrafine Online** | ✅ 可达 | `THIS ITEM IS CURRENTLY OUT OF STOCK` | ❌ 搜索页不渲染结果 | ❌ |
| **MPEX** | ❌ **403 Sucuri** | 读 JSON-LD | `/catalogsearch/result/?term={q}`（参数是 `term` 不是 `q`） | ✅ 可信 |
| **Samy's** | ❌ **403 JBossWeb** | JSON-LD 内为文本 `Temporarily Out of Stock`；另有 `Limit N Rolls Per Customer` | `/s/{query}` | ✅ 可信 |
| **Fotocare** | ❌ **403 Cloudflare** | `Stock Status:(Out of Stock)` + `Available In-Store Only` | ❌ | ❌ |
| **Dodd Camera** | ❌ **403 Cloudflare** | JSON-LD 谎报 `InStock`，真实状态在页面文本 `Backorder` | `/catalogsearch/result/index/?q={q}` | ❌ **撒谎** |

后四家的 403 是本轮新出现的，headless 同样过不去。这**不是**它们下架或没货。

> 2026-09-16 晚些时候换了一个网络复测：MPEX、Fotocare、Dodd 直连全部可达（Fotocare 读出
> `Available In-Store Only`，Dodd 读出 `Backorder`），B&H headless 也能过挑战并读到 JSON-LD；
> Samy's 和 Adorama 仍然 403。同一天、同一份代码，两个网络的结论就差这么多，再次说明
> 可达性表只能和出口 IP 一起看。

### Freestyle 是唯一预告到货日的商家

`Due: Oct 30, 2026` 这类字段全网只有它有，价值最高，务必抓。脚本用
`DUE_RE = Due:\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})` 匹配，命中即升级为 `RESTOCK_DATED`。

### HTML 清洗的两个坑

1. **属性值里的 `>` 会截断标签匹配。** Blue Moon 用 Alpine.js，属性写成
   `@click="modalshow > 0 ? ..."`，朴素的 `<[^>]+>` 会在 `>` 处断开，把整段 JS
   当正文漏进状态窗口，污染取价。剥标签的正则必须先吃掉带引号的属性值。
2. **非贪婪的 `</script>` 会早停。** 脚本里出现字符串 `"</scr"+"ipt>"` 时，
   `<script.*?</script>` 会在假结束标签处收尾，剩余脚本正文泄漏成"可见文案"。

### 价格提取技巧

价格几乎总是**紧邻库存文案**，而不是紧邻商品名（商品名常在导航/面包屑区，附近没有
价格）。先定位状态文案，再在其前后约 `[-360, +280]` 字符窗口内取**第一个**价格
（"相关商品"的价格排在后面）。

⚠️ 当状态词命中的是导航栏或页脚时（Ace Photo、Hunt's、Ultrafine 实测会这样），
窗口里全是菜单文字，取到的价格**不可信**。`raw` 字段里出现大段导航词
（"bags and cases bag straps..."）就是这种情况的信号。

---

## Headless（4 家）

### 统一启动参数

```python
b = _launch_browser(p)   # 优先 channel="chrome"，不可用时回退 Playwright Chromium
ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 1100},
                    locale="en-US", timezone_id="America/New_York")
ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                    "window.chrome={runtime:{}};")
```

| 商家 | 2026-09-16 | 要点 |
|---|---|---|
| **Unique Photo** | ✅ 可达 | ⚠️ JSON-LD 谎报 `InStock`，真实状态是页面文本 `Backordered - On Allocation`。站内搜索不可用（返回全目录） |
| **KEH** | ⚠️ 首个页面可达 | 见下 |
| **B&H** | ❌ 403 | 卡在 `Just a moment...`，轮询到超时仍未放行 |
| **Adorama** | ❌ 403 | 返回空 body（`len=0`）。可达时 JSON-LD 准确，会给出 `BackOrder` / `Discontinued` |

### KEH：从"无解"改判

此前记录为"Cloudflare 持续 403，直连/headless/有头/养会话/换网络均失败"。
2026-09-16 复核**不成立**：headless 打开的**第一个** KEH 页面顺利通过挑战并渲染出
完整商品页（`HTTP 200`，title 正常，读到真实文案）。

但同一 context 内接着请求第 2、3 个 URL 就又被挑战（`Performing security verification`）。

同日换网络再测，表现反过来了：**新 context 的第一个请求吃到 `Error 1015`（HTTP 429，
"You are being rate limited"），同一 context 里接着开的第 2、3 个页面反而正常渲染**，
而且换 URL 顺序也是第一个被拦。两次实测的共同点是 KEH 对同一 IP 的请求节奏很敏感。
脚本现在碰到 1015/429 会冷却约 12 秒后原地重试一次，实测三个 SKU 都能读出。

另一个坑：KEH 缺货页上**没有本品价格**，页面里出现的全是"相关商品 / 最近浏览"的相机
价格（几千美元）。旧逻辑锚点找不到就退回全文取最高价，会输出 `$3998` 这种值。现在
锚点补了 `MODEL #`，并且不再退回全文，取不到就是 `None`。

实测缺货原文：

```
FUJIFILM Fujichrome RDPIII 135-36 Provia 100F (ISO 100) 35mm Color Positive Film
MODEL #361610  We're sorry - this item is temporarily unavailable.
Want to be notified when it comes back in stock? Add it to your wishlist ...
```

⚠️ 这段文字里既没有 `out of stock` 也没有 `backorder`，却含有 `back in stock`。
旧规则会一路落到 `\bin stock\b` 上，把它判成 `IN_STOCK`。因此 `OUT_OF_STOCK` 规则
补入 `temporarily unavailable` / `notified when ... back in stock` / `add to wishlist`，
并给 `in stock` 加上 `(?<!back )` 前瞻排除。

---

## Dakis / Avina 平台（15 家）

**平台归属判定最省事的办法**：直接 GET 首页 HTML（不必渲染），搜
`avina.mydakis.com/embed/<store-uuid>`，命中即属该平台，顺带拿到店铺 UUID。

2026-09-16 用此法确认的 15 家（店铺 UUID 已写入 `stores.json` 的 `store_uuid`）：

George's Camera、Bergen County Camera、Paul's Photo、Service Photo、
Spartan Photo Center、Fromex Photo、Mike's Camera、Biggs Cameras、
Murphy's Camera、Arlington Camera、Cardinal Camera、Competitive Cameras、
Dan's Camera City、Milford Photo、Schiller's Camera

后 11 家此前不在监控内（其中 6 家还被误放进 `delisted`，站点其实仍在运营）。

商品 URL 构造：`https://<域名>/shop/<任意slug>/<商品UUID>` —— **slug 可以随便填，
只有 UUID 起作用**。商品 UUID 全平台通用，见 `stores.json` 的 `dakis_uuids`。

| 商品 | UUID |
|---|---|
| Provia 100F Professional 135-36 | `5c50798b-9597-4b63-bf21-0dda2bd84a8a` |
| Provia 100F Professional 120 | `de6b2b6c-979e-45e4-a661-6043366337fb` |
| Provia 100F RDP-III 120 | `1b90581b-09bd-4bff-a667-2c070d12a3f8` |
| Velvia 50 135 - 36 Exposures | `463658f8-c0f2-4eb6-8a0b-8b87d8d838a8` |
| Velvia 50 RVP 120 - Roll | `68044c70-64b9-0130-87d9-20cf30bab63e` |
| Velvia RVP 50 - 120 5 pack | `0292ba20-9f8f-0138-9fc1-00163ecd2826` |

页面可能有以下三种表现（该平台对任何 UUID 都返回 HTTP 200，状态码不能单独判断）：

1. 渲染出商品名与价格 → 该店有此 SKU
2. 出现 `The requested product is no longer available.` → 曾售，已下架
3. 返回固定长度的空模板页 → 可能未上架，也可能是 Avina 脚本未加载

脚本用假 UUID `00000000-0000-0000-0000-000000000000` 测该店空模板基线长度；
基线仅帮助定位空模板，不能证明商品未上架。空模板目前输出 `UNKNOWN`。

### ⚠️ 2026-09-16 整层不可读

`avina.mydakis.com` 对本网络**所有路径**（`main.js`、`theme.css` 及各种猜测的
JSON 端点）一律返回 403。商品页外壳 HTTP 200 能拿到，但商品内容由该域名的 JS 注入，
于是页面渲染为空 —— **与"空模板 = 从未上架"在外观上完全一致**。

这是个危险的静默失败：空模板不能作为“都没上架”的证据。脚本现在返回 `UNKNOWN`。
**在 Avina 不可达的网络下，Dakis 层的库存结论都未经核实。**

未验证的店在 `stores.json` 里 `carries: []` 且 `verified: false`，脚本会把全部已知
商品 UUID 都探一遍；验证通过后再把实际结果写回 `carries`。

---

## 状态归一化映射表

页面原文 → 归一化状态（脚本 `STATUS_RULES` 的依据，按优先级排列，先匹配先生效）：

| 归一化 | 实测原文 |
|---|---|
| `DELISTED` | `no longer available` ／ `The requested product is no longer available.` ／ HTTP 404 ／ 软 404 |
| `IN_STORE_ONLY` | `Available In-Store Only` ／ `In store only` ／ `Call store for availability` |
| `BACKORDER` | `Backordered - On Allocation`（Unique Photo）／ `Backorder`（Dodd）／ Adorama JSON-LD 的 `schema.org/BackOrder` ／ Shopify `inventory_policy=continue` 且 `qty<=0` |
| `RESTOCK_DATED` | `Currently out of stock. Due: Oct 30, 2026`（Freestyle 独有）／ `Coming back soon!`（Paul's Photo） |
| `OUT_OF_STOCK` | `Temporarily Out of Stock` ／ `Currently Unavailable` ／ `Sold Out` ／ `Availability: 0` ／ `THIS ITEM IS CURRENTLY OUT OF STOCK` ／ `Stock Status:(Out of Stock)` ／ **`this item is temporarily unavailable`（KEH）** ／ **`notified when it comes back in stock`** ／ **`Add it to your wishlist`** |
| `IN_STOCK` | `In Stock`（须排除 `back in stock`）／ 仅有 `Add to Cart` 且无任何缺货文案 |

### 两个方向的误判都会发生

⚠️ `Add to Cart` 在缺货页上同样存在（Dodd、Unique Photo、Dwayne's 都是），
**必须让缺货规则优先于 `IN_STOCK` 规则**，否则大量误报有货（实测 Samy's 会被误报）。

⚠️ 但反方向的漏报同样致命且目前**尚未解决**：按严重度优先意味着页面任何角落
（"相关商品"、"最近浏览"）出现 `Out of stock`，就会盖掉主商品真实的 `In Stock`——
**这正好漏掉本系统唯一要抓的补货事件**。

两者不是二选一，正解是**先把文本裁剪到主商品容器**（加购表单 / `itemprop` 区域
附近）再套规则，而不是在"按严重度"与"按位置"之间挑一个。这是下一步该做的改造。

---

## 附：其他值得抓的字段

- **`Due: <日期>`** — 只有 Freestyle 提供，全网唯一的补货日期预告，价值最高
- **`Limit N Rolls Per Customer`** — Samy's 有，补货时直接决定能买几卷
- **过期片标记** — Dwayne's 商品名里的 `EXP`、BuyMoreFilm 十余个 2000–2012 年挂单
  （JAN 常为 `4902520191519`）
- **料号 / JAN** — Shopify 站从 `.js` 的 `sku` / `barcode` 直接取；Ace Photo、
  Samy's、Blue Moon 在页面上明示

## 附：已确认下架、不要放回监控源

`shopmoment.com`、`filmphotographystore.com`（两家站内搜索 2026-09-16 实测均为
0 结果）、`richardphotolab.com`、`glazerscamera.com`、`lookingglassphoto.com`、
`bedfords.com`（商品页 404）。

### 查过但确认不售的候选

Precision Camera、Focus Camera、Kenmore Camera、Allen's Camera、Citizens Photo、
Southeastern Camera、Peace Camera、CatLABS —— 站内搜索要么 0 结果，要么命中的是
富士**相机**（X-S20、X-half 等）而非胶卷。

### 尚未纳入的品类：综合电商平台

Amazon / eBay / Walmart 上有大量该胶卷挂单，但绝大多数是第三方卖家的过期片、
高价倒卖或日本直邮，与"美国零售商补货"不是一回事，价格与库存信号都不可比。
**当前有意不纳入**——若要纳入，必须单列一类并强制打过期片标记，不能混进现有排序。
