# -*- coding: utf-8 -*-
# ============================================================================
# 影刀 · 插入代码段(Python) · S2
# 作用：遍历供应商网址，打开 1688 新品页，抓取新品并下载主图
# 入参：lst_targets（来自 S1）
# 出参：lst_items = [{产品链接, 产品标题, 产品图片, 上新时间, 供应商名称, 本地图片}, ...]
#
# 取数三条路，按顺序兜：
#   ① 监听页面自己发的接口（主路径）     —— MTOP 走 JSONP，资源类型是 **script**！
#   ② 拿浏览器 cookie 在 Python 里直调接口（保险）
#   ③ DOM 兜底：读 React fiber 上的 memoizedProps.data，能拿到真实 id/title/gmtCreate
#
# 实测结论（2026-09-16，影刀 6.2.23 + 影刀自带浏览器 / 深圳电信家宽）：
#   · 1688 新品页【不需要登录】就能看到新品，页面正常渲染
#   · 但会被"自动化检测"拦：headless、或带 CDP 标记的浏览器一律吃
#     FAIL_SYS_USER_VALIDATE / RGV587 风控，页面永远停在「加载中」
#   · 影刀自带浏览器是 CEF 真实浏览器（扩展驱动、非 CDP），能正常过
#   · ★ 坑1：ModuleAsyncService 的响应类型是 `script`（JSONP 注入 <script>），
#     不是 XHR/Fetch。start_monitor_network 若只写 "XHR|Fetch" 会把它整个漏掉，
#     表现就是「页面上有 21 张商品图，但一条都没抓到」。所以这里必须用 "All"。
#   · ★ 坑2：影刀浏览器偶尔会返回「网页请求未发现，或者页面无响应」，
#     原来 page.navigate 一抛异常整个店铺就废了。现在跳不动就重建页面重试。
#   · ★ 坑3（v1.4 修）：别用 reload(ignore_cache=True) 补抓！实测它会打断 1688
#     这个重量级 React 应用的加载，导致页面只渲染出零星图片（"页面上有 1 张商品图"），
#     三条取数路线就全都空了。正确顺序是：
#         create("about:blank") → start_monitor_network → navigate(url) → 等商品图出现
#     也就是「先开监听、再一次性导航」，跟 v1.1 能拿到 21 张图的场景一致。
#   · ★ 坑4：加载时间不固定，所以不用固定 sleep，改成**轮询等商品图出现**（自适应）。
#   · ★ 坑5（v1.5）：**必须限速**。实测连续快速跑 18 家店铺后，1688 会对整个会话下手：
#     第 19 家起页面能打开、视口正常、readyState=complete，但商品列表就是不渲染
#     （MTOP 全部 RGV587），三条取数路线全空 —— 而且**后面每一家都恢复不了**。
#     所以加了「每家随机间隔 + 每 N 家长休息」，并会检测风控特征、尝试关掉验证码弹窗。
#   · ★ 坑6（v1.5）：实测在影刀浏览器里 **①监听 0/18 成功、②直调 0/18 成功（全 RGV587）**，
#     真正扛住的是 **③DOM(React fiber) 兜底：18/18 成功，每家 20 条**。
#     所以取数顺序改成「DOM 优先 + 滚动累加」，监听和直调降级为补充/兜底。
#   · ★ 坑7（v1.6）：被风控标记的是【会话】，不是 IP！
#     实测：同一出口 IP 下，用一个全新配置的普通 Chrome 打开同一个店铺页，
#     商品图 21 张、日期面板完整、无验证码 —— 一切正常。
#     所以解法是**换掉影刀浏览器的会话 cookie**（PURGE_COOKIE），而不是等 IP 解封。
#     ⚠️ 清 cookie 会一并清掉登录态。推荐顺序：先清一次恢复 → 再在影刀浏览器里登录 1688
#     （登录态的风控阈值高得多）→ 然后把 PURGE_COOKIE 关掉，保住登录态。
#   · ★ 坑8（v1.6）：**影刀里写文件可能是"静默失败"的**！
#     实测 D:\rpa_1688_img 目录权限没问题（外部程序能正常写入），
#     但影刀跑完 360 条却一张图、一份诊断文件都没落盘，且日志里**没有任何报错**。
#     所以写入后一律 `os.path.exists` 复检并打日志，别再假设"没报错就是成功"。
#   · ★ v1.7：抓取结果额外落盘成 `IMG_DIR\抓取结果.json`。
#     抓 28 家要 ~20 分钟，落盘后万一 S4 写入出错，可以只重跑 S4 而不用重抓。
#   · ★ v1.8：支持**按天数筛选**（默认最近 7 天），并且：
#     - 运行时会弹框让你改天数（`ASK_DAYS`，定时运行请改 False）
#     - 超出天数的商品**不下载主图**，省掉大量时间（实测 535 张图要 ~7 分钟）
#     - S3 会按同一口径正式过滤（DAYS_LIMIT 在第 2 段定义，S3 直接复用）
# ============================================================================
VER = "v1.8"

# ----------------------------- 配置区（第2段专用） -----------------------------
BROWSER  = "cef"                  # cef=影刀浏览器；也可填 chrome / edge
IMG_DIR  = r"D:\rpa_1688_img"     # 主图下载目录（写完会复检，见 save_img）
SCROLL   = 4                      # 每个店铺下滑加载次数（DOM 累加靠它，越大越全）
MAX_SHOP = 0                      # 0=跑全部供应商；调试时改成 2
WAIT_MAX = 40                     # 单店最多等多少秒（出现商品图就提前往下走）

DAYS_LIMIT = 7                    # ★ 只要最近 N 天上新的商品；0 = 不限（全都要）
ASK_DAYS   = True                 # ★ 运行时弹框让你改天数（默认值就是 DAYS_LIMIT）
                                  #   无人值守/定时跑请改成 False，免得流程卡在弹框上

PURGE_COOKIE = True               # ★ 启动时清掉 1688 的 cookie，换一个干净会话
                                  #   被风控标记后靠它恢复。跑通并登录 1688 后建议改 True→False

# ---- 限速（防 1688 风控，v1.5 新增；宁可慢也别被封） ----
DELAY_MIN  = 12                   # 每家之间随机等待下限（秒）
DELAY_MAX  = 26                   # 上限
SCROLL_GAP = (2.5, 5.0)           # 每次滚动后的随机停顿（秒）
BATCH_SIZE = 8                    # 每跑 N 家来一次长休息（0=不休息）
BATCH_REST = 150                  # 长休息时长（秒）
SLOW_ON_BLOCK = True              # 检测到风控就自动放慢并长休息
FAIL_STREAK_LIMIT = 5             # 连续这么多家拿不到数据就中止（疑似整体被封，别白等）
# ---------------------------------------------------------------------------------

import xbot, os, re, sys, json, time, random, datetime, hashlib, urllib.parse, urllib.request

APPKEY = "12574478"                       # 1688 H5 固定 appKey
API    = "mtop.alibaba.alisite.cbu.server.ModuleAsyncService"
APIURL = ("https://h5api.m.1688.com/h5/mtop.alibaba.alisite.cbu.server"
          ".moduleasyncservice/1.0/")
COMPONENT_KEY = "wp_pc_new_product_list"
MONITOR_URL   = "ModuleAsyncService"      # 按 URL 模糊匹配，只看这个接口

# 诊断：每家的取数细节都会攒在这里，跑完写进 IMG_DIR\_诊断.txt（以后排查不用截图）
DIAG = []
LAST = {}

# 主图优先级：imageURI 是原图，size* 是缩略图
IMG_KEYS = ["imageURI", "originalImageURI", "size1000x1000ImageURI", "size800x800ImageURI",
            "size520x520ImageURI", "size400x400ImageURI", "size310x310ImageURI"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# --------------------------- 抓取范围：最近 N 天 ---------------------------
def days_cutoff(n):
    """「最近 n 天」的起始毫秒时间戳，**按自然日算且含今天**。
    例：今天 9/17、n=7 → 起点 9/11 00:00:00（覆盖 9/11~9/17 共 7 个自然日）。
    """
    today0 = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today0 - datetime.timedelta(days=max(0, int(n) - 1))
    return int(start.timestamp() * 1000)


def ask_days(default):
    """弹输入框让用户选天数；取消/异常/填了非法值都回落到默认值"""
    try:
        r = xbot.app.dialog.show_input_dialog(
            "新品上新采集",
            "抓最近几天的上新？（直接确定 = %d 天；填 0 = 不限）" % default,
            "input", value=str(default), storage_key="rpa_newproduct_days")
        v = ""
        if isinstance(r, dict):
            for k in ("value", "text", "input", "result", "content"):
                if r.get(k) not in (None, ""):
                    v = str(r[k])
                    break
            if not v:                                  # 字段名对不上时兜底：找第一个数字
                for x in r.values():
                    if isinstance(x, str) and x.strip().isdigit():
                        v = x
                        break
        v = v.strip()
        if v:
            n = int(float(v))
            if 0 <= n <= 3650:
                return n
    except Exception as e:
        xbot.app.logging.warning("天数选择框未生效，按默认 %d 天（%s）" % (default, str(e)[:90]))
    return default


# ------------------------------- 小工具 -------------------------------
def norm_img(u):
    """//cbu01.xxx 或 img/ibank/xxx 都补成完整 https 地址（接口给的是相对路径）"""
    if not u:
        return ""
    if isinstance(u, (list, tuple)):
        for x in u:
            r = norm_img(x)
            if r:
                return r
        return ""
    u = str(u).strip()
    if u.startswith("http"):
        return u
    if u.startswith("//"):
        return "https:" + u
    return "https://cbu01.alicdn.com/" + u.lstrip("/")


def jsonp2obj(body):
    """先按纯 JSON 解析，失败再剥 mtopjsonpN( ... ) 的壳"""
    if not body:
        return None
    s = str(body).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("("), s.rfind(")")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i + 1:j])
        except Exception:
            return None
    return None


def deep_find_offers(obj, depth=0):
    """
    递归找 offerModuleList。
    实测真实结构是 data.data.offerModuleList（MTOP 外壳 data + 业务 data 两层），
    但接口偶有改动，递归找最稳。
    """
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        v = obj.get("offerModuleList")
        if isinstance(v, list):
            return v
        for vv in obj.values():
            r = deep_find_offers(vv, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for vv in obj[:5]:
            r = deep_find_offers(vv, depth + 1)
            if r is not None:
                return r
    return None


def pick_img(offer):
    for im in (offer.get("offerImages") or []):
        if not isinstance(im, dict):
            continue
        for k in IMG_KEYS:
            if im.get(k):
                return norm_img(im[k])
        for v in im.values():
            if isinstance(v, str) and re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", v, re.I):
                return norm_img(v)
    return ""


def to_ms(v):
    """上新时间 → 毫秒时间戳。实测接口给的是 '2026-09-16 15:36:00' 这种字符串"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    s = s.replace("/", "-").split(".")[0].strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(s[:19], f)) * 1000)
        except Exception:
            pass
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    if m:
        try:
            return int(time.mktime(time.strptime("%s-%s-%s" % (
                m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)), "%Y-%m-%d")) * 1000)
        except Exception:
            pass
    return None


def offer_to_item(offer, shop_name):
    oid = str(offer.get("id") or "").strip()
    if not oid:
        return None
    # 接口原版字段是 subject；DOM 兜底那份是 title
    title = (offer.get("subject") or offer.get("title") or "").strip()
    return {
        F_LINK: "https://detail.1688.com/offer/%s.html" % oid,
        F_TITLE: title,
        F_IMG: pick_img(offer) or norm_img(offer.get("imgUrl")),
        F_TIME: to_ms(offer.get("gmtCreate")),
        F_SHOP: shop_name,
    }


def collect_from_responses(resps, shop_name, seen):
    """解析一批报文，抽出入参里要的 4 个字段"""
    got = []
    for r in (resps or []):
        if not isinstance(r, dict):
            continue
        body = r.get("body")
        if not body and isinstance(r.get("offerModuleList"), list):
            body = json.dumps(r, ensure_ascii=False)
        obj = jsonp2obj(body)
        if not obj:
            continue
        for off in (deep_find_offers(obj) or []):
            if not isinstance(off, dict):
                continue
            it = offer_to_item(off, shop_name)
            if it and it[F_LINK] not in seen:
                seen.add(it[F_LINK])
                got.append(it)
    return got


# ------------------------- 路线②：用浏览器 cookie 直调接口 -------------------------
def browser_cookies(page):
    """从影刀浏览器取 1688 相关 cookie，拼成 Cookie 头 + 取出 _m_h5_tk 令牌"""
    try:
        cks = page.get_cookies() or []
    except Exception as e:
        xbot.app.logging.warning("取 cookie 失败：%s" % e)
        return "", ""
    pairs, token = [], ""
    for c in cks:
        if not isinstance(c, dict):
            continue
        dom = c.get("domain") or ""
        if not any(k in dom for k in ("1688", "alibaba", "alicdn")):
            continue
        name, val = c.get("name"), c.get("value")
        if not name:
            continue
        pairs.append("%s=%s" % (name, val))
        if name == "_m_h5_tk":
            token = str(val).split("_")[0]
    return "; ".join(pairs), token


JS_MEMBER = """
function () {
  try {
    var h = document.documentElement ? document.documentElement.outerHTML : '';
    var m = h.match(/sellerMemberId=([A-Za-z0-9\\-]+)/);
    if (m) { return m[1]; }
    m = h.match(/(b2b-[A-Za-z0-9]{8,})/);
    if (m) { return m[1]; }
    var es = performance.getEntriesByType('resource') || [];
    for (var i = 0; i < es.length; i++) {
      var u = decodeURIComponent(es[i].name || '');
      m = u.match(/memberId["':=\\s]+(b2b-[A-Za-z0-9]+)/);
      if (m) { return m[1]; }
    }
  } catch (e) {}
  return '';
}
"""


def page_member_id(page):
    """多路抠 memberId（形如 b2b-2214485988262e6c32）：
    ① get_html() 源码  ② 页面 DOM/JS 变量  ③ 已发出的接口请求 URL
    —— 页面没渲染出来时也能拿到，路线②才真正独立于渲染
    """
    try:
        html = page.get_html() or ""
    except Exception:
        html = ""
    m = (re.search(r"sellerMemberId=([A-Za-z0-9\-]+)", html)
         or re.search(r"(b2b-[A-Za-z0-9]{8,})", html))
    if m:
        return m.group(1)
    for world in ("MAIN", "ISOLATED"):
        try:
            v = page.execute_javascript(JS_MEMBER, execution_world=world)
            if v:
                return str(v).strip()
        except Exception:
            continue
    return ""


def fetch_member_id_via_http(cookie_str, shop_url):
    """终极兜底：拿浏览器 cookie 直接 GET 店铺页 HTML，从中抠 memberId。
    完全不依赖影刀浏览器渲染 —— 就算页面白屏，路线②也能跑起来。
    """
    if not cookie_str:
        return ""
    try:
        req = urllib.request.Request(shop_url, headers={
            "User-Agent": UA, "Cookie": cookie_str,
            "Referer": "https://www.1688.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "ignore")
        m = (re.search(r"sellerMemberId=([A-Za-z0-9\-]+)", html)
             or re.search(r"(b2b-[A-Za-z0-9]{8,})", html))
        if m:
            xbot.app.logging.info("HTTP 兜底抠到 memberId：%s" % m.group(1))
            return m.group(1)
        xbot.app.logging.warning("HTTP 拿到 %d 字节 HTML，但没找到 memberId" % len(html))
    except Exception as e:
        xbot.app.logging.warning("HTTP 抠 memberId 失败：%s" % str(e)[:120])
    return ""


def mtop_fetch(cookie_str, token, member_id, shop_url, page_index=1):
    """照页面的样子拼一次 MTOP GET，签名 = md5(token & t & appKey & data)"""
    params = json.dumps({"memberId": member_id, "appdata": {"pageIndex": page_index}},
                        ensure_ascii=False, separators=(",", ":"))
    data_map = {"componentKey": COMPONENT_KEY, "params": params}
    data_str = "&".join("%s=%s" % (k, v) for k, v in data_map.items())

    t = str(int(time.time() * 1000))
    sign = hashlib.md5(("%s&%s&%s&%s" % (token, t, APPKEY, data_str)).encode("utf-8")).hexdigest()
    q = {"jsv": "2.7.5", "appKey": APPKEY, "t": t, "sign": sign, "api": API,
         "v": "1.0", "type": "GET", "dataType": "json", "valueType": "string", "ecode": "0"}
    q.update(data_map)

    req = urllib.request.Request(APIURL + "?" + urllib.parse.urlencode(q), headers={
        "User-Agent": UA, "Referer": shop_url,
        "Origin": "https://" + shop_url.split("/")[2],
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_str,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------- 路线③：DOM 兜底（读 React fiber） ---------------------
# 页面是 react-pi-v2 渲染的，商品卡不是 <a>，div 上也没有 id。
# 但 React 会把 props 挂在 DOM 节点的 __reactInternalInstance$xxx 上，
# 顺着 fiber 往上找 memoizedProps.data，就能拿到 {id, title, gmtCreate, imgUrl}。
JS_DOM = """
function () {
  var out = [], seen = {};
  var imgs = document.querySelectorAll('img[src*="/img/ibank/"]');
  for (var i = 0; i < imgs.length; i++) {
    var el = imgs[i], obj = null, ks = Object.keys(el);
    for (var a = 0; a < ks.length && !obj; a++) {
      var k = ks[a];
      if (k.indexOf('__reactInternalInstance') === 0 || k.indexOf('__reactFiber') === 0) {
        var f = el[k];
        for (var j = 0; j < 15 && f; j++) {
          var p = f.memoizedProps;
          if (p && p.data && typeof p.data === 'object' && p.data.id) { obj = p.data; break; }
          f = f.return;
        }
      }
    }
    if (!obj) continue;
    var id = String(obj.id);
    if (!id || seen[id]) continue;
    seen[id] = 1;
    var iu = obj.imgUrl, img = '';
    if (iu && iu.length) { img = iu[0]; }
    else if (typeof iu === 'string') { img = iu; }
    out.push({ id: id, title: obj.title || obj.subject || '',
               gmtCreate: obj.gmtCreate || '', img: img });
  }
  return JSON.stringify(out);
}
"""


def dom_extract(page, shop_name, seen):
    """返回 (items, 页面上商品图数量)"""
    raw = None
    for world in ("MAIN", "ISOLATED"):          # MAIN=网页环境，能读到 React 挂的属性
        try:
            raw = page.execute_javascript(JS_DOM, execution_world=world)
            if raw:
                break
        except Exception as e:
            xbot.app.logging.warning("DOM 提取(%s)失败：%s" % (world, e))
    got = []
    try:
        arr = json.loads(raw) if raw else []
    except Exception:
        arr = []
    for d in arr:
        oid = str(d.get("id") or "")
        if not oid:
            continue
        it = {
            F_LINK: "https://detail.1688.com/offer/%s.html" % oid,
            F_TITLE: (d.get("title") or "").strip(),
            F_IMG: norm_img(d.get("img")),
            F_TIME: to_ms(d.get("gmtCreate")),
            F_SHOP: shop_name,
        }
        if it[F_LINK] not in seen:
            seen.add(it[F_LINK])
            got.append(it)
    return got


# ------------------------------ 抓单个店铺 ------------------------------
JS_PROBE = """
function () {
  var w = 0, h = 0;
  try { w = window.innerWidth || 0; h = window.innerHeight || 0; } catch (e) {}
  var o = {
    url: location.href, title: document.title || '', ready: document.readyState,
    w: w, h: h,
    imgs: document.querySelectorAll('img[src*="/img/ibank/"]').length,
    cards: document.querySelectorAll('.timeline').length
  };
  return JSON.stringify(o);
}
"""


def probe(page):
    """一次 JS 拿回页面状态（url / 视口 / 商品图数量），判断页面渲染到哪一步"""
    for world in ("MAIN", "ISOLATED"):
        try:
            raw = page.execute_javascript(JS_PROBE, execution_world=world)
            if raw:
                return json.loads(raw)
        except Exception:
            continue
    return {}


# --------------------- 风控识别与「叉掉验证码」 ---------------------
JS_BLOCK = """
function () {
  try {
    var u = location.href, b = document.body;
    var t = b ? (b.innerText || '') : '';
    return JSON.stringify({
      punish: /_____tmd_____|punish|x5secdata/i.test(u),
      slider: !!document.querySelector('#nc_1_wrapper,.nc-container,#nc_1_n1z,.nc_scale,.baxia-dialog'),
      verify: /请稍后重试|安全验证|滑动验证|拖动|访问受限|被挤爆|验证码/.test(t),
      txt: t.replace(/\\s+/g, ' ').slice(0, 100)
    });
  } catch (e) { return ''; }
}
"""

# 关掉验证码/风控弹窗：点常见关闭按钮 + 隐藏全屏遮罩层
JS_KILL_POPUP = """
function () {
  var n = 0;
  var sels = ['#nc_1_wrapper .btn_close', '.nc_iconfont.btn_close', '.nc-container .close',
              '[class*="close-btn"]', '[class*="dialog"] [class*="close"]',
              '.next-dialog-close', '.baxia-dialog .close', '.sufei-dialog-close',
              '[aria-label="关闭"]', '[title="关闭"]'];
  for (var i = 0; i < sels.length; i++) {
    var els = document.querySelectorAll(sels[i]);
    for (var j = 0; j < els.length; j++) {
      try { els[j].click(); n++; } catch (e) {}
    }
  }
  var ov = document.querySelectorAll('.nc-container,.nc_scale,#nc_1_wrapper,.baxia-dialog,[class*="mask"]');
  for (var k = 0; k < ov.length; k++) {
    try {
      var st = ov[k].getAttribute('style') || '';
      if (/position:\\s*fixed/i.test(st) || /nc_|baxia|mask/.test(ov[k].className || '')) {
        ov[k].style.display = 'none'; n++;
      }
    } catch (e) {}
  }
  return n;
}
"""


def check_block(page):
    """检测页面是否被风控（滑块/验证码/拦截提示）。返回 dict 或 None"""
    for world in ("MAIN", "ISOLATED"):
        try:
            raw = page.execute_javascript(JS_BLOCK, execution_world=world)
            if raw:
                return json.loads(raw)
        except Exception:
            continue
    return None


def try_kill_popup(page):
    """出现验证码弹窗时试着叉掉它（点关闭按钮 + 隐藏遮罩）"""
    try:
        n = page.execute_javascript(JS_KILL_POPUP, execution_world="MAIN")
        if n:
            xbot.app.logging.warning("检测到风控弹窗，已尝试关闭 %s 个元素" % n)
            time.sleep(2.5)
        return int(n or 0)
    except Exception:
        return 0


JS_PURGE = """
function () {
  try {
    var ks = document.cookie ? document.cookie.split(';') : [];
    for (var i = 0; i < ks.length; i++) {
      var nm = ks[i].split('=')[0].trim();
      if (!nm) { continue; }
      document.cookie = nm + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
      document.cookie = nm + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=.1688.com';
      document.cookie = nm + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=.taobao.com';
      document.cookie = nm + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=.alibaba.com';
    }
    return ks.length;
  } catch (e) { return 0; }
}
"""


def purge_1688_cookies(page):
    """清掉 1688 相关 cookie，换一个干净会话。

    ★ 为什么有效：实测同一出口 IP 下，全新配置的普通 Chrome 打开同一个店铺页
      完全正常（21 张商品图、无验证码）—— 说明被风控标记的**不是 IP，是这个会话**。
    """
    n, names = 0, set()
    # ① 先访问 1688 首页，让 cookie 落在正确域下，才能读到/删掉
    try:
        page.navigate("https://www.1688.com/")
        time.sleep(4)
    except Exception as e:
        xbot.app.logging.warning("访问 1688 首页失败（不影响继续）：%s" % str(e)[:100])

    # ② 页面对象方式：拿全部 cookie，逐个删
    try:
        for c in (page.get_cookies() or []):
            if not isinstance(c, dict):
                continue
            dom = c.get("domain") or ""
            nm = c.get("name")
            if not nm or not any(k in dom for k in ("1688", "alibaba", "alicdn", "taobao")):
                continue
            names.add(nm)
    except Exception as e:
        xbot.app.logging.warning("读取 cookie 失败：%s" % str(e)[:90])

    for nm in names:
        try:
            page.remove_cookie(nm)
            n += 1
        except Exception:
            pass

    # ③ 兜底：用 JS 再过一遍（清 path/domain 变体）
    try:
        j = page.execute_javascript(JS_PURGE, execution_world="MAIN")
        n += int(j or 0)
    except Exception:
        pass

    # ④ 再兜底：模块级 API（不需要页面在目标域上）
    for u in ("https://www.1688.com", "https://h5api.m.1688.com", "https://detail.1688.com"):
        try:
            for c in (xbot.web.get_cookies(u, mode=BROWSER) or []):
                if isinstance(c, dict) and c.get("name"):
                    try:
                        xbot.web.remove_cookie(u, c["name"], mode=BROWSER)
                        n += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return n


def new_page(shop_name):
    """新建一个空白页面，失败重试 3 次"""
    err = None
    for i in range(3):
        try:
            return xbot.web.create("about:blank", mode=BROWSER,
                                   load_timeout=30, stop_if_timeout=False)
        except Exception as e:
            err = e
            xbot.app.logging.warning("%s：新建页面失败(第%d/3次)（%s）"
                                     % (shop_name, i + 1, str(e)[:150]))
            time.sleep(5)
    raise Exception("无法新建页面（%s）" % err)


def grab_shop(page, url, shop_name, scroll_times):
    """返回 (items, page)。page 可能被重建，调用方要接住这个新对象。"""
    seen, items = set(), []
    LAST.clear()
    LAST.update({"fresh": False, "monitor": False, "resp1": 0, "resp2": 0,
                 "imgs": None, "route": "无", "err": "", "wait": 0, "block": False,
                 "block_txt": "", "kill": 0, "url": "", "ready": "", "vw": "",
                 "title": "", "cards": None})

    # ---- ① 先开监听，再一次性导航过去（顺序不能反，也不能用 reload 补）----
    fresh = False
    if page is None:
        page = new_page(shop_name)
        fresh = True
    LAST["fresh"] = fresh

    nav_ok = False
    for attempt in (1, 2):
        try:
            # resource_type 必须含 Script：MTOP 走 JSONP，资源类型是 script，不是 XHR
            page.start_monitor_network(url=MONITOR_URL, resource_type="All")
            LAST["monitor"] = True
        except Exception as e:
            LAST["err"] = ("监听开启失败:%s" % e)[:80]
            xbot.app.logging.warning("开启网络监听失败：%s" % e)
        try:
            page.navigate(url)
            nav_ok = True
            break
        except Exception as e:
            xbot.app.logging.warning("%s：跳转失败(第%d次)，重建页面（%s）"
                                     % (shop_name, attempt, str(e)[:150]))
            try:
                page.close()
            except Exception:
                pass
            page = new_page(shop_name)
            fresh = True
            LAST["fresh"] = True
    if not nav_ok:
        LAST["route"] = "跳转失败"
        LAST["err"] = "navigate 两次都失败"
        return items, page

    # ---- ② 自适应等页面：轮询到商品图出现就往下走（加载快慢不固定，别用固定 sleep）----
    t0 = time.time()
    n_img = None
    while time.time() - t0 < WAIT_MAX:
        time.sleep(1.5)
        info = probe(page)
        if info:
            LAST["url"] = (info.get("url") or "")[:120]
            LAST["title"] = (info.get("title") or "")[:60]
            LAST["ready"] = info.get("ready") or ""
            LAST["vw"] = "%sx%s" % (info.get("w"), info.get("h"))
            LAST["cards"] = info.get("cards")
            n_img = info.get("imgs")
            if isinstance(n_img, int) and n_img > 3:
                break
            # 页面已经加载完、却几乎没有商品图 → 再等也不会变，提前退出走兜底路线
            # （省时间：否则 28 家每家都要白等满 WAIT_MAX 秒）
            if (info.get("ready") == "complete" and isinstance(n_img, int)
                    and n_img <= 1 and time.time() - t0 > 7):
                LAST["early"] = True
                break
        # 顺便早读一次监听：响应体放久了可能被浏览器回收
        try:
            early = page.get_responses(url=MONITOR_URL) or []
            if early:
                items = collect_from_responses(early, shop_name, seen)
                LAST["resp1"] = len(early)
                if items:
                    break
        except Exception:
            pass
    LAST["imgs"] = n_img
    LAST["wait"] = round(time.time() - t0, 1)
    xbot.app.logging.info("%s：页面就绪 %.1fs，商品图 %s 张，视口 %s"
                          % (shop_name, LAST["wait"], n_img, LAST["vw"]))

    # ---- ★ 风控识别：页面打开了但商品不渲染，基本就是被拦了 ----
    blk = check_block(page)
    if blk:
        LAST["block"] = bool(blk.get("punish") or blk.get("slider") or blk.get("verify"))
        LAST["block_txt"] = (blk.get("txt") or "")[:70]
        if LAST["block"]:
            LAST["kill"] = try_kill_popup(page)     # 先试着把验证码弹窗叉掉
            if SLOW_ON_BLOCK:
                rest = random.uniform(45, 75)
                xbot.app.logging.warning("%s：疑似风控（%s），休息 %.0fs 后重试一次"
                                         % (shop_name, LAST["block_txt"] or "弹窗/滑块", rest))
                time.sleep(rest)
                try:
                    page.navigate(url)              # 休息后重载，给它一次恢复机会
                    time.sleep(7)
                    info = probe(page)
                    if info:
                        LAST["imgs"] = info.get("imgs")
                except Exception:
                    pass

    # ---- ③ 取数：DOM 优先（实测 18/18 成功），滚动累加；监听同时补充 ----
    items = dom_extract(page, shop_name, seen)          # 首屏
    resps_a = []
    try:
        resps_a = page.get_responses(url=MONITOR_URL) or []
        items.extend(collect_from_responses(resps_a, shop_name, seen))
    except Exception as e:
        LAST["err"] = ("监听读取失败:%s" % e)[:80]
    LAST["resp1"] = max(LAST["resp1"], len(resps_a))

    for k in range(scroll_times):                       # 滚动翻页：DOM + 监听一起累加
        try:
            page.scroll_to(location="bottom", behavior="instant")
        except Exception:
            pass
        time.sleep(random.uniform(*SCROLL_GAP))
        before = len(items)
        items.extend(dom_extract(page, shop_name, seen))
        try:
            resps_b = page.get_responses(url=MONITOR_URL) or []
            LAST["resp2"] = max(LAST["resp2"], len(resps_b))
            items.extend(collect_from_responses(resps_b, shop_name, seen))
        except Exception:
            pass
        if len(items) == before and k >= 1:
            break                                       # 连续两次没新增，提前收工
    try:
        page.stop_monitor_network()
    except Exception:
        pass

    if items:
        LAST["route"] = "①监听" if (LAST["resp1"] or LAST["resp2"]) else "③DOM"
        xbot.app.logging.info("%s：拿到 %d 条（%s）" % (shop_name, len(items), LAST["route"]))
        return items, page

    # ---- ④ DOM 也没拿到 → 再试 cookie 直调（实测基本无效，留作最后手段）----
    try:
        ck, tk = browser_cookies(page)
        mid = page_member_id(page)
        if not mid and ck:                       # 页面没渲染出来时的终极兜底
            mid = fetch_member_id_via_http(ck, url)
        if ck and mid:
            for pi in range(1, 3):
                obj = mtop_fetch(ck, tk, mid, url, pi)
                got = collect_from_responses([{"body": json.dumps(obj, ensure_ascii=False)}],
                                             shop_name, seen)
                if not got:
                    LAST["err"] = ("直调ret=%s" % (obj or {}).get("ret"))[:80]
                    xbot.app.logging.warning("%s：接口直调第%d页无数据，ret=%s"
                                             % (shop_name, pi, (obj or {}).get("ret")))
                    break
                items.extend(got)
            if items:
                LAST["route"] = "②直调"
                xbot.app.logging.warning("%s：走接口直调补到 %d 条" % (shop_name, len(items)))
                return items, page
        else:
            LAST["err"] = "cookie=%s/memberId=%s" % (bool(ck), bool(mid))
    except Exception as e:
        LAST["err"] = ("直调异常:%s" % e)[:80]

    xbot.app.logging.warning(
        "%s：没拿到数据。商品图 %s 张，监听 %s/%s 条，页面 %s%s"
        % (shop_name, LAST["imgs"], LAST["resp1"], LAST["resp2"],
           (LAST["url"] or "")[:55], "，疑似风控" if LAST.get("block") else ""))
    return items, page


def save_img(url, path):
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": "https://www.1688.com/"})
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read()
        if len(b) < 1024:
            return ""
        with open(path, "wb") as f:
            f.write(b)
        if not os.path.exists(path):          # 影刀里写入可能"静默失败"，必须复检
            xbot.app.logging.warning("图片写入后不存在（疑似被拦截）：%s" % path)
            return ""
        return path
    except Exception as e:
        xbot.app.logging.warning("主图下载失败：%s（%s）" % (url, e))
        return ""


# ---------------------------------- 执行 ----------------------------------
os.makedirs(IMG_DIR, exist_ok=True)

# ★ 目录可写性自检：影刀里写文件可能「静默失败」（不报错但文件不存在），先探一次
_probe = os.path.join(IMG_DIR, "_写权限自检.tmp")
try:
    with open(_probe, "w", encoding="utf-8") as _f:
        _f.write("ok")
    if os.path.exists(_probe):
        xbot.app.logging.info("图片目录可写 ✓ %s" % IMG_DIR)
        os.remove(_probe)
    else:
        xbot.app.logging.warning("⚠️ 写入后文件不存在！%s —— 图片和诊断文件可能都存不下来" % IMG_DIR)
except Exception as _e:
    xbot.app.logging.warning("⚠️ 图片目录不可写 %s：%s" % (IMG_DIR, _e))
xbot.app.logging.info("运行目录 cwd=%s | Python %s" % (os.getcwd(), sys.version.split()[0]))

shops = lst_targets if isinstance(lst_targets, list) else []
if MAX_SHOP > 0:
    shops = shops[:MAX_SHOP]
if not shops:
    raise Exception("S2：入参 lst_targets 为空，请先跑 S1")

page = None          # 空白页交给 new_page 建，导航统一在 grab_shop 里做
lst_items = []
fail_streak = 0
xbot.app.logging.info("S2 %s 启动：店铺 %d 家，等待上限 %ds/家，单店下滑 %d 次"
                      % (VER, len(shops), WAIT_MAX, SCROLL))
xbot.app.logging.info("限速：每家间隔 %.0f~%.0f 秒，每 %d 家休息 %d 秒；连续 %d 家空手就中止"
                      % (DELAY_MIN, DELAY_MAX, BATCH_SIZE, BATCH_REST, FAIL_STREAK_LIMIT))

# ★ 抓取范围：默认最近 7 天，运行时可弹框改（S3 会按同一口径正式过滤）
if ASK_DAYS:
    DAYS_LIMIT = ask_days(DAYS_LIMIT)
CUT_MS = days_cutoff(DAYS_LIMIT) if DAYS_LIMIT > 0 else 0
if CUT_MS:
    xbot.app.logging.info("抓取范围：最近 %d 天上新的商品（%s 起）"
                          % (DAYS_LIMIT,
                             datetime.datetime.fromtimestamp(CUT_MS / 1000).strftime("%Y-%m-%d")))
else:
    xbot.app.logging.info("抓取范围：不限天数（全都要）")

# ★ 换一个干净会话再开始：被风控标记的是 cookie，不是 IP（见文件头「坑7」）
if PURGE_COOKIE:
    try:
        page = new_page("初始化")
        _n = purge_1688_cookies(page)
        xbot.app.logging.info("已清理 1688 相关 cookie（%d 项），换用干净会话开始采集" % _n)
        try:
            page.navigate("about:blank")
        except Exception:
            pass
    except Exception as e:
        xbot.app.logging.warning("清 cookie 流程失败，按原会话继续：%s" % str(e)[:120])

try:
    for i, s in enumerate(shops, 1):
        name, site = s.get(F_SHOP, ""), s.get("网址", "")
        try:
            got, page = grab_shop(page, site, name, SCROLL)
        except Exception as e:
            xbot.app.logging.error("[%d/%d] %s 采集异常：%s" % (i, len(shops), name, e))
            DIAG.append({"序号": i, "供应商": name, "网址": site, "条数": 0,
                         "路线": "异常", "错误": str(e)[:150]})
            page = None          # 页面已不可信，下一家重新开
            continue
        skipped_img = 0
        for it in got:
            # ★ 超出天数的商品不下载主图 —— 省掉大量时间（实测 535 张图要 ~7 分钟）
            if CUT_MS and (it.get(F_TIME) or 0) < CUT_MS:
                it["本地图片"] = ""
                skipped_img += 1
                continue
            m = re.search(r"offer/(\d+)", it[F_LINK])
            oid = m.group(1) if m else str(len(lst_items))
            it["本地图片"] = save_img(it[F_IMG], os.path.join(IMG_DIR, oid + ".jpg"))
        lst_items.extend(got)
        row = {"序号": i, "供应商": name, "网址": site, "条数": len(got)}
        row.update(LAST)
        DIAG.append(row)
        if skipped_img:
            xbot.app.logging.info("[%d/%d] %s → %d 条（其中 %d 条超出 %d 天，跳过下载主图）"
                                  % (i, len(shops), name, len(got), skipped_img, DAYS_LIMIT))
        else:
            xbot.app.logging.info("[%d/%d] %s → %d 条" % (i, len(shops), name, len(got)))

        # ★ 熔断：连续多家空手 = 整个会话被风控了，再跑也是白跑，提前收工
        fail_streak = 0 if got else fail_streak + 1
        if fail_streak >= FAIL_STREAK_LIMIT:
            xbot.app.logging.warning(
                "连续 %d 家没拿到数据，判断已被整体风控 —— 提前结束本次采集"
                "（已抓到的 %d 条会照常写入）。建议休息 30 分钟以上或换个时间再跑。"
                % (fail_streak, len(lst_items)))
            break

        # ★ 限速：连续快速跑会被 1688 判定成爬虫（实测跑到第 19 家时整个会话被拦，
        #   之后每家都渲染不出商品，而且不会自己恢复）。宁可慢也别被封。
        if i < len(shops):
            if BATCH_SIZE and i % BATCH_SIZE == 0:
                rest = float(BATCH_REST)
                xbot.app.logging.info("已跑 %d 家，长休息 %d 秒防风控…" % (i, rest))
            else:
                rest = random.uniform(DELAY_MIN, DELAY_MAX)
            time.sleep(rest)
finally:
    try:
        if page is not None:
            page.close()
    except Exception:
        pass

# ---- 诊断文件：每家的取数细节落盘，排查不用再截图 ----
diag_path = os.path.join(IMG_DIR, "诊断.txt")
try:
    with open(diag_path, "w", encoding="utf-8") as f:
        f.write("S2 %s | 店铺 %d 家，抓到 %d 条\n" % (VER, len(shops), len(lst_items)))
        f.write("字段说明：\n"
                "  wait    等页面耗时(秒)      fresh  本次是否新建了页面\n"
                "  monitor 监听是否开成功      resp1/resp2  两次读到的监听报文条数\n"
                "  imgs    页面商品图数量      vw     浏览器视口大小\n"
                "  url     页面实际地址（若不是 1688 店铺页就说明被跳转了）\n"
                "  cards   .timeline 日期锚点数量（>0 说明商品列表已渲染）\n"
                "  block   是否疑似被风控      kill   尝试关闭的验证码弹窗元素数\n"
                "  block_txt 页面上的拦截提示文字\n"
                "  route   生效的取数路线：①监听 / ②直调 / ③DOM / 跳转失败 / 无\n"
                "  err     失败原因\n")
        f.write("-" * 110 + "\n")
        for d in DIAG:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    if os.path.exists(diag_path):
        xbot.app.logging.info("诊断文件：%s（%d 字节）" % (diag_path, os.path.getsize(diag_path)))
    else:
        xbot.app.logging.warning(
            "⚠️ 诊断文件写入后竟然不存在！%s —— 说明影刀环境里写文件被静默拦截了，"
            "图片同样会丢。请把这一行连同「图片目录可写」那行一起发我" % diag_path)
except Exception as e:
    xbot.app.logging.warning("写诊断文件失败：%s" % e)

xbot.app.logging.info("S2 合计抓取 %d 条" % len(lst_items))

# ---- 抓取结果落盘：抓一次要 20 分钟，存下来后 S4 出问题就不用重抓 ----
# 双写：IMG_DIR + 备份目录，防止其中一个位置被清理
_bak_dir = os.path.join(os.path.expanduser("~"), "rpa_1688_img_backup")
for _d in (IMG_DIR, _bak_dir):
    data_path = os.path.join(_d, "抓取结果.json")
    try:
        os.makedirs(_d, exist_ok=True)
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(lst_items, f, ensure_ascii=False)
        if os.path.exists(data_path):
            xbot.app.logging.info("抓取结果已落盘：%s（%d 条，%d 字节）"
                                  % (data_path, len(lst_items), os.path.getsize(data_path)))
        else:
            xbot.app.logging.warning("⚠️ 抓取结果落盘后不存在：%s" % data_path)
    except Exception as e:
        xbot.app.logging.warning("抓取结果落盘失败（%s）：%s" % (_d, e))

if not lst_items:
    raise Exception("S2：一条都没抓到。诊断文件已写到 %s，把它发我" % diag_path)
