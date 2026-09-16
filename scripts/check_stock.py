#!/usr/bin/env python3
"""
Provia 100F / Velvia 50 登记商家的库存与价格采集器。

用法:
  # 推荐：先激活任意已安装依赖的 Python 环境，再使用通用启动器
  ./check_stock.sh --all --json out.jsonl

  # 兼容入口：动态查找 Conda，默认使用 base；可用 FILM_CONDA_ENV 改环境名
  ./check_stock_base.sh --all --json out.jsonl

  python3 check_stock.py                      # 抓所有无需渲染的商家（shopify + html + api）
  python3 check_stock.py --method shopify     # 只抓某一类
  python3 check_stock.py --store freestyle,keh# 只抓指定商家
  python3 check_stock.py --all                # 含 headless（B&H/Adorama/UniquePhoto/KEH）与 Dakis
  python3 check_stock.py --json out.jsonl     # 额外输出 JSONL
  python3 check_stock.py --diff prev.jsonl    # 与上一轮快照比对，单列新增线索
  python3 check_stock.py --discover <host>    # 对某 Shopify 域名做 SKU 发现后退出

退出码：0=无新增购买或到货线索 ｜ 10=有新增购买或到货线索 ｜
        20=无可买线索且有未核实项，不能断言无货。

状态归一化（按告警价值从高到低）:
  IN_STOCK       真有货，立即告警
  BACKORDER      缺货但可下单排队
  RESTOCK_DATED  缺货但商家给了到货日（restock_date 字段）
  OUT_OF_STOCK   纯缺货
  IN_STORE_ONLY  线上不卖，仅到店/电话
  DELISTED       商品已下架（应从监控源移除）
  BLOCKED        被反爬拦截（必须告警，不可当作没货）
  UNKNOWN        抓到了页面但没识别出状态
"""
import argparse, json, os, random, re, sys, time, html as htmllib
import html.parser as htmlparser
import urllib.request, urllib.error, urllib.parse   # parse 必须显式导入：shopify_discover 用到 quote()
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = json.load(open(os.path.join(HERE, "..", "stores.json"), encoding="utf-8"))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
# Accept-Encoding: identity 是必须的——部分站点压缩响应会导致解码乱码
HDR = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
       "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "identity"}

PRICE_RE = re.compile(r"\$\s?([0-9]{1,4}(?:,[0-9]{3})?\.[0-9]{2})")
DUE_RE = re.compile(r"Due:\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})")
LIMIT_RE = re.compile(r"Limit\s+(\d+)\s+Roll", re.I)

# 页面可见文案 → 归一化状态。顺序即优先级，先匹配先生效。
# 关键：这里全部基于实测抓到的原文，改动前务必先拿真实页面回归。
STATUS_RULES = [
    ("DELISTED",      r"no longer available|requested product is no longer|this item is no longer"),
    ("IN_STORE_ONLY", r"available in-?store only|in store only|call store for availability|please call[^.]{0,40}for availability"),
    ("BACKORDER",     r"backordered\s*-\s*on allocation|\bbackorder(ed)?\b|back-?ordered"),
    # 2026-09-16 补：KEH 缺货页原文是 "this item is temporarily unavailable"，
    # 且紧跟 "notified when it comes back in stock"。旧规则两条都不匹配，
    # 最终落到 IN_STOCK 的 "\bin stock\b" 上，会把缺货误报成有货。
    ("OUT_OF_STOCK",  r"temporarily out of stock|currently out of stock|this item is currently out of stock"
                      r"|temporarily unavailable|item is unavailable|out of stock|sold out"
                      r"|availability:\s*0|currently unavailable"
                      r"|notify me when available|notify when available|add (it )?to (your )?wishlist"),
    # (?<!back ) 防止 "back in stock"（缺货页的订阅提醒文案）被当成有货
    ("IN_STOCK",      r"(?<!back )\bin stock\b|add to cart"),
]


def norm_status(text):
    low = " ".join(text.split()).lower()
    for status, pat in STATUS_RULES:
        if re.search(pat, low):
            return status
    return "UNKNOWN"


# 用正则剥 HTML 标签在这批页面上反复出事，全部来自属性值与正文的边界判断：
#   · Blue Moon 用 Alpine.js，属性写成 @click="modalshow > 0 ? ..."，
#     朴素的 <[^>]+> 会在属性内的 ">" 处断开，把整段 JS 当正文漏进来；
#   · Hunt's 的注释 <!-- ... if one doesn't exist ----> 里有撇号，
#     一旦让引号参与配对就会一路吃到下一个注释，单次吞掉 3.5KB 正文
#     （连同 "Out of Stock" 一起），把缺货误判成 UNKNOWN；
#   · Blue Moon 的 Livewire wire:snapshot 属性塞着整段转义 JSON，长达数千字符。
# 这些是 HTML 解析问题，不是正则调参问题。标准库的 HTMLParser 本来就正确处理
# 引号、注释与 CDATA，没有额外依赖，直接用它。
class _TextExtractor(htmlparser.HTMLParser):
    """把 HTML 抽成可见文案，丢弃 script/style 等不可见内容。"""

    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif not self._skip_depth:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def flatten(html_src):
    p = _TextExtractor()
    try:
        p.feed(html_src)
        p.close()
    except Exception:
        # HTMLParser 对畸形文档极少抛异常；真抛了就退回粗暴剥标签，
        # 宁可拿到脏文本，也不要整店变成 BLOCKED。
        return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", html_src)))
    return re.sub(r"\s+", " ", "".join(p.parts))


# ---- Shopify 全局限速器 -------------------------------------------------
# 实测：Shopify 的 429 限流是【跨店按 IP】生效的，且覆盖整个店铺前台
# （不只是 .js / products.json 端点，普通商品页一样 429）。一旦触发，
# 9 家 Shopify 店会同时不可用，且需要较长时间才恢复。
# 因此这里用一个全局令牌间隔，宁可慢也不要把 IP 打进小黑屋。
import threading
_SHOPIFY_LOCK = threading.Lock()
_SHOPIFY_LAST = [0.0]
SHOPIFY_MIN_INTERVAL = float(os.environ.get("FILM_SHOPIFY_INTERVAL", "4.0"))


def _shopify_gate():
    with _SHOPIFY_LOCK:
        wait = SHOPIFY_MIN_INTERVAL - (time.time() - _SHOPIFY_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _SHOPIFY_LAST[0] = time.time()


def fetch(url, timeout=35, retries=5, gated=False):
    """429 退避重试。gated=True 时先过全局限速器（Shopify 专用）。"""
    delay = 5
    for attempt in range(retries):
        if gated:
            _shopify_gate()
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(delay + random.uniform(0, 2))   # 加抖动，避免同步重试
                delay = min(delay * 2, 60)
                continue
            raise


def extract_price(window, page_html=""):
    """先从状态文案附近取价，取不到再退回 JSON-LD。"""
    prices = PRICE_RE.findall(window)
    if prices:
        # 商品自身价格通常是窗口内第一个，"相关商品"价格排在后面
        return float(prices[0].replace(",", ""))
    m = re.search(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?', page_html)
    return float(m.group(1)) if m else None


def status_window(text, span=(360, 280)):
    """定位状态文案，返回状态、取价窗口和简短证据。

    两条铁律：
    1) 严格按 STATUS_RULES 的顺序取第一个命中，即"按严重度优先"，
       绝不能改成"取页面中最早出现的"。导航栏、相关商品区里到处是
       "Add to Cart"，按位置取会把缺货商品误判成有货——这是本系统
       最危险的错误（实测 Samy's 会被误报为 IN_STOCK）。
    2) 窗口围绕状态词而非商品名：价格几乎总是紧邻状态词，
       商品名多出现在面包屑/标题区，附近没有价格。
    """
    low = " ".join(text.split()).lower()
    for status, pat in STATUS_RULES:
        m = re.search(pat, low)
        if m:
            window = low[max(0, m.start() - span[0]): m.start() + span[1]]
            evidence = low[m.start(): m.end() + 140]
            return status, window, evidence
    return "UNKNOWN", text[:900], text[:170]


def progress(msg):
    """进度打到 stderr，不污染 stdout 的结果表。

    一轮全量在被限流时可以跑十几分钟且全程零输出，而本工具要抓的补货窗口
    只有几小时——看不出它是在退避还是挂死，是很实际的问题。
    """
    print(msg, file=sys.stderr, flush=True)


def rec(store, sku, url, **kw):
    # 输出 method 而非旧的 tier：tier 在 stores.json 里的取值与 reference.md
    # 的 A/B/C/D 访问难度分级互相矛盾（Shopify 店被标成 B/C，文档里却是 A 级），
    # 而 method 本来就唯一决定了采集路径，是真正有信息量的那个字段。
    d = {"store": store["id"], "store_name": store["name"], "method": store.get("method"),
         "sku": sku, "url": url, "status": "UNKNOWN", "price": None,
         "restock_date": None, "purchase_limit": None,
         "mfr": None, "jan": None, "raw": None,
         "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    d.update(kw)
    return d


# ---------------------------------------------------------------- shopify
def check_shopify(store):
    """Shopify: <商品URL>.js 直接返回 available 布尔值，最稳最快。"""
    out = []
    for sku, handle in store["skus"].items():
        url = f"https://{store['host']}/products/{handle}"
        try:
            code, body = fetch(url + ".js", gated=True)
            d = json.loads(body)
        except urllib.error.HTTPError as e:
            out.append(rec(store, sku, url,
                           status="DELISTED" if e.code == 404 else "BLOCKED", raw=f"HTTP {e.code}"))
            continue
        except Exception as e:
            out.append(rec(store, sku, url, status="BLOCKED", raw=type(e).__name__))
            continue
        variants = d.get("variants") or []
        out.append(rec(store, sku, url, **_shopify_status(d, variants)))
    return out


def _shopify_status(d, variants):
    """把 Shopify 的 available 布尔值翻译成真实可得性。

    陷阱（2026-09-16 实测 Pro Camera Hawaii）：Shopify 的 `available` 只表示
    "能不能加购"，不表示"有没有现货"。当卖家设了 inventory_policy="continue"
    （允许超卖）时，inventory_quantity=0 也照样返回 available=true。
    直接把 available 当 IN_STOCK，会在零库存店上持续误报有货——正是本系统
    最不能犯的错。因此只有「确实有库存数」才算 IN_STOCK，超卖挂单归 BACKORDER。
    """
    avail = [v for v in variants if v.get("available")]
    if not (avail or d.get("available")):
        # 缺货也要带上料号与 JAN：跨店去重靠的就是这两个键，
        # 只在有货时才记会让缺货记录无法与别家对齐。
        v0 = variants[0] if variants else {}
        return {"status": "OUT_OF_STOCK", "price": round((d.get("price") or 0) / 100, 2),
                "mfr": v0.get("sku") or None, "jan": v0.get("barcode") or None,
                "raw": d.get("title", "")[:90]}

    # 取第一个可购变体的价格，避免商品级 price 与实际可买变体不一致
    v = avail[0] if avail else {}
    price = round((v.get("price") or d.get("price") or 0) / 100, 2)
    qty, policy = v.get("inventory_quantity"), v.get("inventory_policy")
    managed = v.get("inventory_management")

    if managed and policy == "continue" and (qty or 0) <= 0:
        status, note = "BACKORDER", f"超卖挂单 qty={qty} policy=continue"
    elif managed and (qty or 0) <= 0:
        status, note = "OUT_OF_STOCK", f"qty={qty}"
    else:
        status, note = "IN_STOCK", (f"qty={qty}" if qty is not None else "未启用库存管理")

    title = d.get("title", "")[:70]
    return {"status": status, "price": price,
            "mfr": v.get("sku") or None, "jan": v.get("barcode") or None,
            "raw": f"{title} | {note}"}


def shopify_discover(host, queries=("velvia 50", "provia 100f", "velvia", "provia")):
    """用 suggest.json 发现新 SKU。limit 上限 10，命中 10 条要换更细的词再查。"""
    found = {}
    failed = 0
    for q in queries:
        u = (f"https://{host}/search/suggest.json?q={urllib.parse.quote(q)}"
             f"&resources[type]=product&resources[limit]=10")
        try:
            _, body = fetch(u, gated=True)
            for p in json.loads(body)["resources"]["results"]["products"]:
                if re.search(r"velvia|provia", p["title"], re.I):
                    found[p["url"].split("?")[0]] = (p["title"], p.get("available"), p.get("price"))
        except Exception:
            failed += 1
    if failed == len(queries):
        raise RuntimeError(f"{host}: Shopify 搜索请求全部失败，无法判断是否有商品")
    return found


# ---------------------------------------------------------------- html
def check_html(store):
    out = []
    for sku, url in store["urls"].items():
        try:
            code, body = fetch(url)
        except urllib.error.HTTPError as e:
            out.append(rec(store, sku, url,
                           status="DELISTED" if e.code == 404 else "BLOCKED", raw=f"HTTP {e.code}"))
            continue
        except Exception as e:
            out.append(rec(store, sku, url, status="BLOCKED", raw=type(e).__name__))
            continue

        text = flatten(body)
        status, window, evidence = status_window(text)

        # JSON-LD 仅在该店被标记为可信时作为兜底
        if status == "UNKNOWN" and store.get("jsonld_trusted"):
            ld = re.search(r'"availability"\s*:\s*"([^"]+)"', body)
            if ld:
                v = ld.group(1).lower()
                status = ("IN_STOCK" if "instock" in v else
                          "BACKORDER" if "backorder" in v else
                          "DELISTED" if "discontinued" in v else "OUT_OF_STOCK")
                evidence = f"JSON-LD availability={ld.group(1)}"

        due = DUE_RE.search(text)
        lim = LIMIT_RE.search(text)
        if due and status == "OUT_OF_STOCK":
            status = "RESTOCK_DATED"

        out.append(rec(store, sku, url, status=status,
                       price=extract_price(window, body),
                       restock_date=due.group(1) if due else None,
                       purchase_limit=int(lim.group(1)) if lim else None,
                       raw=evidence.strip()[:170]))
    return out


# ---------------------------------------------------------------- dwaynes
def check_dwaynes(store):
    """Dwayne's 有私有 JSON API 给价格，但没有库存字段。"""
    out = []
    for sku, pid in store["product_ids"].items():
        api = f"https://www.dwaynesphoto.com/v1/products/{pid}.json"
        url = store["urls"].get(sku, api)
        try:
            _, body = fetch(api)
            d = json.loads(body)
            price = d.get("price")
            out.append(rec(store, sku, url, status="UNKNOWN",
                           price=float(price) if price is not None else None,
                           raw=f"{d.get('name','')[:70]} | API 无库存字段，需核实商品页"))
        except Exception as e:
            out.append(rec(store, sku, url, status="BLOCKED", raw=type(e).__name__))
    return out


# ---------------------------------------------------------------- headless
def check_headless(stores):
    """B&H / Adorama / Unique Photo / Dakis —— 必须渲染。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [rec(s, "*", "", status="BLOCKED",
                    raw="playwright 未安装：pip install playwright && playwright install chromium")
                for s in stores]

    out = []
    uuids = REGISTRY["dakis_uuids"]
    with sync_playwright() as p:
        try:
            b = _launch_browser(p)
        except Exception as e:
            return [rec(s, "*", "", status="BLOCKED",
                        raw=f"浏览器启动失败：{type(e).__name__}: {str(e)[:90]}")
                    for s in stores]
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 1100},
                            locale="en-US", timezone_id="America/New_York")
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                            "window.chrome={runtime:{}};")
        pg = ctx.new_page()

        for store in stores:
            if store["method"] == "dakis":
                baseline = _dakis_baseline(pg, store["host"], uuids["_bogus_probe"])
                # carries 为空 = 尚未验证该店上架了什么，不是"什么都没上架"。
                # 这种店要把全部已知商品 UUID 都探一遍，否则新加的店会一条都不产出，
                # 在报告里表现为"这家店不存在"——正是最容易漏掉补货的方式。
                carries = store.get("carries") or [k for k in uuids if not k.startswith("_")]
                for sku in carries:
                    uid = uuids.get(sku)
                    if not uid:
                        continue
                    url = f"https://{store['host']}/shop/x/{uid}"
                    out.append(_dakis_one(pg, store, sku, url, baseline))
                continue

            for sku, url in store["urls"].items():
                out.append(_headless_one(pg, store, sku, url))
        b.close()
    return out


def _launch_browser(playwright):
    """启动可移植的 Chromium。

    FILM_BROWSER_CHANNEL 有值时使用指定 channel（例如 chrome）。未指定时，
    优先使用本机 Chrome；若 Chrome 不可用，再回退到通过
    `playwright install chromium` 安装的浏览器。
    """
    options = {"headless": True,
               "args": ["--disable-blink-features=AutomationControlled"]}
    channel = os.environ.get("FILM_BROWSER_CHANNEL")
    if channel:
        return playwright.chromium.launch(channel=channel, **options)

    try:
        return playwright.chromium.launch(channel="chrome", **options)
    except Exception as chrome_error:
        try:
            return playwright.chromium.launch(**options)
        except Exception:
            raise chrome_error


def _wait_cf(pg, tries=6, step=4000):
    """Cloudflare 挑战：轮询到 title 不再是 'Just a moment...' 才算通过。"""
    for _ in range(tries):
        pg.wait_for_timeout(step)
        if "just a moment" not in (pg.title() or "").lower():
            return True
    return False


def _cf_error(text):
    """Cloudflare 错误页（1015 限流、1020 拒绝等）的错误码；不是错误页返回 None。

    这类页面可能以 200 或 429 返回，标题也不是 "Just a moment"，正文只有
    "Error 1015 Ray ID: ..."。2026-09-16 KEH 实测曾被记成 UNKNOWN，其实是被拦。
    """
    head = text[:600].lower()
    m = re.search(r"\berror (10\d\d)\b", head) if "ray id" in head else None
    return m.group(1) if m else None


def _headless_one(pg, store, sku, url, cooldown=12000):
    try:
        for attempt in range(2):
            resp = pg.goto(url, timeout=70000, wait_until="domcontentloaded")
            code = resp.status if resp else None
            _wait_cf(pg)
            pg.wait_for_timeout(2500)
            text = re.sub(r"\s+", " ", pg.evaluate("()=>document.body.innerText"))
            page_html = pg.content()
            # 2026-09-16 KEH 实测：新 context 的第一个请求会吃到 1015 限流，
            # 同一 context 里接着开第二、三个页面反而正常。所以碰到 1015/429
            # 不急着放弃，冷却一下原地再试一次。
            if attempt == 0 and (code == 429 or _cf_error(text)):
                pg.wait_for_timeout(cooldown)
                continue
            break
    except Exception as e:
        return rec(store, sku, url, status="BLOCKED", raw=f"{type(e).__name__}: {str(e)[:60]}")

    cf_err = _cf_error(text)
    if code in (403, 429) or "just a moment" in (pg.title() or "").lower() or cf_err:
        why = f"Cloudflare error {cf_err}" if cf_err else f"HTTP {code} / CF challenge"
        return rec(store, sku, url, status="BLOCKED", raw=why)
    if code == 404 or re.search(r"\b404\b.{0,40}(not found|getting lost)", text[:900], re.I):
        return rec(store, sku, url, status="DELISTED", raw="404 / soft-404")

    status = "UNKNOWN"
    evidence = ""
    if store.get("jsonld_trusted"):
        ld = re.search(r'"availability"\s*:\s*"([^"]+)"', page_html)
        if ld:
            v = ld.group(1).lower()
            status = ("IN_STOCK" if "instock" in v else
                      "BACKORDER" if "backorder" in v else
                      "DELISTED" if "discontinued" in v else "OUT_OF_STOCK")
            evidence = f"JSON-LD availability={ld.group(1)}"
    status_win = ""
    if status == "UNKNOWN":
        status, status_win, evidence = status_window(text[:4000])
    else:
        _, status_win, _ = status_window(text[:4000])

    # 价格只在料号锚点附近取，取不到再看状态文案附近。绝不退回全文：
    # 2026-09-16 KEH 实测，缺货页上根本没有本品价格，全文里的都是
    # "相关商品 / 最近浏览" 的相机价格，退回全文会输出 $3998 这类离谱值。
    m = re.search(r"(MFR ?#|MODEL ?#|SKU:|MFR:)", text)
    win = text[max(0, m.start() - 420): m.start() + 300] if m else ""
    prices = sorted(set(PRICE_RE.findall(win)), key=lambda x: float(x.replace(",", "")))
    if prices:
        price = float(prices[-1].replace(",", ""))
    else:
        near = PRICE_RE.findall(status_win)
        price = float(near[0].replace(",", "")) if near else None
    due = DUE_RE.search(text)
    return rec(store, sku, url, status=status,
               price=price,
               restock_date=due.group(1) if due else None,
               raw=(evidence or win)[:150])


def _dakis_baseline(pg, host, bogus_uuid):
    """用假 UUID 测出该店"未上架"时的空模板长度，作为判别基线。"""
    try:
        pg.goto(f"https://{host}/shop/x/{bogus_uuid}", timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_timeout(9000)
        return len(re.sub(r"\s+", " ", pg.evaluate("()=>document.body.innerText")))
    except Exception:
        return -1


def _dakis_one(pg, store, sku, url, baseline):
    try:
        pg.goto(url, timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_timeout(10000)
        text = re.sub(r"\s+", " ", pg.evaluate("()=>document.body.innerText"))
    except Exception as e:
        return rec(store, sku, url, status="BLOCKED", raw=type(e).__name__)

    if re.search(r"no longer available|requested product", text, re.I):
        return rec(store, sku, url, status="DELISTED", raw="Dakis: no longer available")
    if not re.search(r"provia|velvia", text, re.I):
        note = "空模板" if baseline > 0 and abs(len(text) - baseline) < 60 else f"未渲染 len={len(text)}"
        # Avina 脚本被拦时也会呈现空模板，不能据此断定商品已下架。
        return rec(store, sku, url, status="UNKNOWN", raw=f"Dakis: {note}，无法确认库存")

    m = re.search(r"(Provia|Velvia)", text, re.I)
    win = text[max(0, m.start() - 80): m.start() + 420]
    prices = sorted(set(PRICE_RE.findall(win)), key=lambda x: float(x.replace(",", "")))
    return rec(store, sku, url, status=norm_status(win),
               price=float(prices[-1].replace(",", "")) if prices else None,
               raw=win[:150])


# ---------------------------------------------------------------- main
DISPATCH = {"shopify": check_shopify, "html": check_html, "dwaynes_api": check_dwaynes}
ORDER = ["IN_STOCK", "BACKORDER", "RESTOCK_DATED", "BLOCKED",
         "OUT_OF_STOCK", "IN_STORE_ONLY", "DELISTED", "UNKNOWN"]
# 三种值得提醒的线索：现货、允许排队下单、已公布到货日。
ACTIONABLE = ("IN_STOCK", "BACKORDER", "RESTOCK_DATED")


def load_snapshot(path):
    """读入上一轮 JSONL 快照，键为 (store, sku)。"""
    prev = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            prev[(r["store"], r["sku"])] = r
    return prev


def diff_report(prev, results):
    """对比上一轮快照，分出新增线索 / 线索消失 / 新监控项 / 未抓取项。

    补货窗口只有几小时到几天，所以"跳变"本身才是告警，绝对状态是次要的。
    BLOCKED 和 UNKNOWN 不参与跳变判定：未核实的状态不能证明库存变化。
    """
    restock, gone, added, vanished = [], [], [], []
    seen = set()
    for r in results:
        key = (r["store"], r["sku"])
        seen.add(key)
        old = prev.get(key)
        if old is None:
            if r["status"] in ACTIONABLE:
                added.append((None, r))
            continue
        # 上轮未核实 → 本轮有线索：不能证明发生了补货，单独列为新发现。
        if old["status"] in ("BLOCKED", "UNKNOWN"):
            if r["status"] in ACTIONABLE:
                added.append((old, r))
            continue
        if r["status"] in ("BLOCKED", "UNKNOWN"):
            continue
        if old["status"] not in ACTIONABLE and r["status"] in ACTIONABLE:
            restock.append((old, r))
        elif old["status"] in ACTIONABLE and r["status"] not in ACTIONABLE:
            gone.append((old, r))
    for key, old in prev.items():
        if key not in seen and old["status"] in ACTIONABLE:
            vanished.append((old, None))
    return restock, gone, added, vanished


def print_diff(prev, results):
    restock, gone, added, vanished = diff_report(prev, results)
    print("\n" + "=" * 108)
    if restock:
        print("### 🔔 新增购买或到货线索（由已知无货状态转入）")
        for old, r in restock:
            pr = f"${r['price']:.2f}" if r["price"] else ""
            print(f"  {old['status']} → {r['status']}  {r['store_name']} {r['sku']} {pr}"
                  f" {r.get('restock_date') or ''}\n      {r['url']}")
    else:
        print("### 无已证实的状态转变。")
    if added:
        print("\n### 新监控项或上轮未核实，本轮发现购买或到货线索：")
        for _, r in added:
            print(f"  [{r['status']}] {r['store_name']} {r['sku']}  {r['url']}")
    if gone:
        print("\n### 购买或到货线索消失：")
        for old, r in gone:
            print(f"  {old['status']} → {r['status']}  {r['store_name']} {r['sku']}")
    if vanished:
        print("\n### 上轮可买、本轮未抓取（可能被过滤掉了，不等于没货）：")
        for old, _ in vanished:
            print(f"  [{old['status']}] {old['store_name']} {old['sku']}")
    return restock, added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=("shopify", "html", "dwaynes_api", "headless", "dakis", "blocked"),
                    help="只检查指定采集方式")
    ap.add_argument("--store", help="逗号分隔的 store id")
    ap.add_argument("--all", action="store_true", help="含 headless / dakis（可能耗时数十分钟）")
    ap.add_argument("--json", help="额外写出 JSONL")
    ap.add_argument("--diff", metavar="PREV.jsonl",
                    help="与上一轮快照比对，单独列出购买或到货线索")
    ap.add_argument("--discover", metavar="HOST",
                    help="对某个 Shopify 域名跑 SKU 发现，然后退出")
    args = ap.parse_args()

    if args.discover:
        try:
            found = shopify_discover(args.discover)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 20
        if not found:
            print(f"{args.discover}: 未发现 Provia/Velvia 商品（或该站不是 Shopify）")
            return 0
        for url, (title, avail, price) in sorted(found.items()):
            print(f"  [{'可购' if avail else '缺货'}] {title[:66]}\n"
                  f"       handle={url.rstrip('/').split('/')[-1]}  price={price}")
        return 0

    stores = REGISTRY["stores"]
    if args.store:
        want = {s.strip() for s in args.store.split(",")}
        missing = want - {s["id"] for s in stores}
        if missing:
            ap.error(f"未知商家 id: {', '.join(sorted(missing))}")
        stores = [s for s in stores if s["id"] in want]
    if args.method:
        stores = [s for s in stores if s["method"] == args.method]
    if not stores:
        ap.error("筛选后没有商家")
    prev = None
    if args.diff:
        try:
            prev = load_snapshot(args.diff)
        except (OSError, ValueError, KeyError) as e:
            ap.error(f"上一轮快照无法读取: {e}")

    fast = [s for s in stores if s["method"] in DISPATCH]
    slow = [s for s in stores if s["method"] in ("headless", "dakis")]
    blocked = [s for s in stores if s["method"] == "blocked"]

    results = []
    if fast:
        # Shopify 的 429 是跨店按 IP 生效的，必须限并发；html/api 站点无此问题
        shop = [s for s in fast if s["method"] == "shopify"]
        rest = [s for s in fast if s["method"] != "shopify"]
        if rest:
            progress(f"[1/3] 直连 {len(rest)} 家（并发 6）…")
            with ThreadPoolExecutor(max_workers=6) as ex:
                for batch in ex.map(lambda s: DISPATCH[s["method"]](s), rest):
                    results.extend(batch)
        t0 = time.time()
        for i, s in enumerate(shop, 1):     # 串行 + 间隔，换取稳定
            progress(f"[2/3] Shopify {i}/{len(shop)} {s['name']}"
                     f"（限速 {SHOPIFY_MIN_INTERVAL}s/请求，已用 {time.time() - t0:.0f}s）")
            results.extend(check_shopify(s))
            time.sleep(1.5)
    if slow and (args.all or args.method in ("headless", "dakis") or args.store):
        progress(f"[3/3] headless/Dakis {len(slow)} 家，每页需渲染 7–13 秒，请耐心…")
        results.extend(check_headless(slow))
    for s in blocked:
        for sku, url in s.get("urls", {}).items():
            results.append(rec(s, sku, url, status="BLOCKED", raw=s.get("note", "")))

    results.sort(key=lambda r: (ORDER.index(r["status"]) if r["status"] in ORDER else 9,
                                r["price"] if r["price"] else 1e9))

    print(f"\n{'状态':<16}{'商家':<24}{'SKU':<9}{'价格':>9}  备注")
    print("-" * 108)
    for r in results:
        pr = f"${r['price']:.2f}" if r["price"] else "—"
        extra = []
        if r.get("restock_date"):
            extra.append(f"到货 {r['restock_date']}")
        if r.get("purchase_limit"):
            extra.append(f"限购 {r['purchase_limit']}")
        note = " ｜ ".join(extra) or (r.get("raw") or "")[:52]
        print(f"{r['status']:<16}{r['store_name'][:22]:<24}{r['sku']:<9}{pr:>9}  {note}")

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\n" + " ｜ ".join(f"{k}={v}" for k, v in
                            sorted(counts.items(), key=lambda x: ORDER.index(x[0]) if x[0] in ORDER else 9)))

    hot = [r for r in results if r["status"] in ("IN_STOCK", "BACKORDER", "RESTOCK_DATED")]
    if hot:
        print("\n### 可下单 / 有到货日：")
        for r in hot:
            print(f"  [{r['status']}] {r['store_name']} {r['sku']} "
                  f"{('$%.2f' % r['price']) if r['price'] else ''} "
                  f"{r.get('restock_date') or ''}\n      {r['url']}")
    else:
        print("\n### 本轮未发现可买或有到货日的商品。")
        if any(r["status"] in ("BLOCKED", "UNKNOWN") for r in results):
            print("部分商品未核实，不能据此断言全部缺货。")

    restock = added = []
    if args.diff:
        restock, added = print_diff(prev, results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n已写出 {len(results)} 条 → {args.json}")

    # 退出码给定时任务用：20 表示无可买线索时仍有未核实项。
    if restock or added:
        return 10
    if not hot and (counts.get("BLOCKED", 0) or counts.get("UNKNOWN", 0)):
        return 20
    return 0


if __name__ == "__main__":
    sys.exit(main())
