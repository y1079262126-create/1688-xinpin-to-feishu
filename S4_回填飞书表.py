# -*- coding: utf-8 -*-
# ============================================================================
# 影刀 · 插入代码段(Python) · S4
# 作用：上传主图 → 写入多维表格「新品上新（下载）」
# 入参：lst_final（来自 S3）
# 出参：int_ok, int_fail, int_skip
#
# ★ 坑1（v1.5 修）：本段曾漏掉 `_req`/`feishu_token`/`txt` 三个函数定义，
#   结果 S1~S3 全正常、数据也抓到了，**最后一步写入全线失败**
#   报「变量 "_req" 未被定义」—— 改工程文件后务必做函数完整性校验。
# ★ 坑2（v1.7 修）：目标表「产品链接」是 **type=text + style.type=url**（超链接样式），
#   写入必须是对象 `{"link": ..., "text": ...}`；塞裸字符串会整批报
#   `1254068 URLFieldConvFail: the value of 'Link' must be an object`
#   （实测 535 条全军覆没）。现在会先探测字段样式再决定怎么写。
# ============================================================================
S4_VER = "v1.7"

# ----------------------------- 配置区（凭证已在第1段配好，这里只放本段开关） ---
try:
    TBL_DST
except NameError:
    TBL_DST = "tbl3BzPbvQ66WjGV"   # 正常由第 1 段总配置区定义     # 目标表：新品上新（下载）
SKIP_DUP = True                   # 目标表已存在的产品链接跳过，重复跑不脏数据
UPLOAD_IMG = True                 # 上传主图（首次验证可先改 False，快很多）
# 注：APP_ID / APP_SECRET / BASE_TOKEN 在第 1 段定义；4 段内联于同一作用域，这里直接复用。
#     ⚠️ 千万不要在本段再写一遍 APP_ID/APP_SECRET —— 内联后会把第 1 段的真实凭证覆盖回占位符。
# ---------------------------------------------------------------------------------

import xbot, os, json, uuid, mimetypes, urllib.request

F_LINK, F_TITLE, F_TIME, F_SHOP, F_IMG = F_LINK, F_TITLE, F_TIME, F_SHOP, F_IMG


# ------------------------------- 飞书基础 -------------------------------
def _req(url, method="GET", token=None, payload=None, raw=None, ctype=None):
    h = {"Content-Type": ctype or "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = "Bearer " + token
    data = raw if raw is not None else (
        json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def feishu_token():
    d = _req("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
             "POST", payload={"app_id": APP_ID, "app_secret": APP_SECRET})
    if d.get("code") != 0:
        raise Exception("飞书鉴权失败：%s" % d)
    return d["tenant_access_token"]


def txt(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return (v.get("link") or v.get("text") or "").strip()
    if isinstance(v, list):
        return "".join(txt(x) for x in v).strip()
    return str(v).strip()


def field_style(tk):
    """拿「字段名 → 样式标识」，用来判断某字段到底要怎么写。

    ★ 坑：「产品链接」这类字段在飞书里是**超链接**，写入时**必须给对象**
      `{"link": ..., "text": ...}`；塞裸字符串会报
      `1254068 URLFieldConvFail: the value of 'Link' must be an object`。

    ★ 坑2（v1.8 修）：fields 接口返回的元数据结构**和我猜的不一样**！
      实测（2026-09-17 真实日志）按 `style.type` 取出来全是空串 → 全被判成纯文本
      → 540 条又整批失败。实际响应里有的是：
      - `ui_type: "Url"`（首字母大写）
      - `type: 15`（飞书固定：15 = 超链接）
      - 少数版本给 `style: {type: "url"}`
      三种都看，命中任意一个就按 URL 处理。
    """
    out = {}
    try:
        d = _req("https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/fields?page_size=200"
                 % (BASE_TOKEN, TBL_DST), "GET", tk)
        for f in ((d.get("data") or {}).get("items") or []):
            nm = f.get("field_name") or f.get("name") or ""
            ui = str(f.get("ui_type") or "").lower()
            st = str((f.get("style") or {}).get("type") or "").lower()
            tp = f.get("type")
            if tp == 15 or ui == "url" or st == "url":
                out[nm] = "url"
            else:
                out[nm] = ui or st or ("type%s" % tp if tp is not None else "")
    except Exception as e:
        xbot.app.logging.warning("读取字段样式失败（按纯文本处理）：%s" % str(e)[:100])
    return out


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def existing_links(tk):
    """目标表里已有的产品链接，用于去重"""
    out, page_token = set(), None
    while True:
        url = ("https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500"
               % (BASE_TOKEN, TBL_DST))
        if page_token:
            url += "&page_token=" + page_token
        d = _req(url, "GET", tk)
        if d.get("code") != 0:
            raise Exception("读取目标表失败：%s" % d)
        dd = d["data"]
        for it in dd.get("items") or []:
            link = txt((it.get("fields") or {}).get(F_LINK))
            if link:
                out.add(link)
        if dd.get("has_more") and dd.get("page_token"):
            page_token = dd["page_token"]
        else:
            return out


def upload_media(tk, path):
    """上传本地图片到飞书素材库，返回 file_token（附件字段要用）"""
    boundary = "----xbot" + uuid.uuid4().hex
    fname = os.path.basename(path)
    size = os.path.getsize(path)
    ctype = mimetypes.guess_type(fname)[0] or "image/jpeg"
    body = b""
    for k, v in (("file_name", fname), ("parent_type", "bitable_file"),
                 ("parent_node", BASE_TOKEN), ("size", str(size))):
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (boundary, k, v)).encode("utf-8")
    body += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (boundary, fname, ctype)).encode("utf-8")
    with open(path, "rb") as f:
        body += f.read()
    body += ("\r\n--%s--\r\n" % boundary).encode("utf-8")

    d = _req("https://open.feishu.cn/open-apis/drive/v1/medias/upload_all", "POST", tk,
             raw=body, ctype="multipart/form-data; boundary=" + boundary)
    if d.get("code") != 0:
        raise Exception("上传图片失败：%s" % d)
    return d["data"]["file_token"]


def batch_create(tk, records):
    """批量写记录，返回 (成功数, 失败数)"""
    ok = fail = 0
    for i in range(0, len(records), 100):                 # 每批 100 条
        chunk = records[i:i + 100]
        try:
            d = _req("https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records/batch_create"
                     % (BASE_TOKEN, TBL_DST), "POST", tk, payload={"records": chunk})
            if d.get("code") == 0:
                ok += len(chunk)
            else:
                fail += len(chunk)
                xbot.app.logging.error("批量写入失败：%s" % d)
        except Exception as e:
            fail += len(chunk)
            xbot.app.logging.error("批量写入异常：%s" % e)
    return ok, fail


# ---------------------------------- 执行 ----------------------------------
# IMG_DIR 由第 2 段定义；单独跑本段时兜底
if "IMG_DIR" not in dir():
    IMG_DIR = r"D:\rpa_1688_img"

# ⚠️ 本段依赖第 1 段定义的 APP_ID / APP_SECRET / BASE_TOKEN（影刀把 4 段内联进同一作用域）。
#    **第 1 段不能禁用** —— 它只花 2 秒读一次供应商表，同时提供全流程唯一的凭证定义。
#    想省时间请只禁用第 2 段（20 分钟的抓取），别禁第 1 段。
#    （也**不要**在本段重复定义凭证：影刀对代码段做静态变量检查，第 1 段被禁用时
#    连 `try: APP_ID` 这种引用都会在 Python 执行前被拦下报「变量未定义」，兜底根本跑不到。
#    2026-09-17 13:57 实测：S3 正常跑完、流程结束之后才报这个错，就是这个机制。）

# 数据文件双位置：IMG_DIR + 备份目录（防其中一个位置被清理）
DATA_DIRS = (IMG_DIR, os.path.join(os.path.expanduser("~"), "rpa_1688_img_backup"))


def read_data_file(name):
    for _d in DATA_DIRS:
        try:
            with open(os.path.join(_d, name), encoding="utf-8") as _f:
                return json.load(_f)
        except Exception:
            continue
    return None


tk = feishu_token()
done = existing_links(tk) if SKIP_DUP else set()

# 入参为空时退回上次抓取落盘的 JSON（方便只重跑本段、不重跑 21 分钟的 S2）
try:
    _src = lst_final
except NameError:
    _src = None              # 前面几段被禁用（单独重跑本段）时，直接用落盘 JSON
data = _src if isinstance(_src, list) and _src else None
if data is None:
    data = read_data_file("抓取结果.json")
    if data:
        xbot.app.logging.warning("lst_final 为空，改用落盘的抓取结果 %d 条" % len(data))
data = data if isinstance(data, list) else []
if not data:
    xbot.app.logging.warning(
        "⚠️ 没有可写入的数据：lst_final 为空，且 %s 里也没有「抓取结果.json」。"
        "请先把第 2 段启用、完整跑一遍抓取" % "、".join(DATA_DIRS))

xbot.app.logging.info("S4 %s 启动：待写入 %d 条，去重=%s，传图=%s"
                      % (S4_VER, len(data), SKIP_DUP, UPLOAD_IMG))

# ★ 按目标表实际样式决定「产品链接」怎么写（超链接样式必须给对象，见 field_style 注释）
STYLE = field_style(tk)
LINK_AS_URL = (STYLE.get(F_LINK) == "url")
xbot.app.logging.info("字段样式 %s → 产品链接按【%s】写入"
                      % (STYLE, "超链接对象" if LINK_AS_URL else "纯文本"))

# 图片 file_token 缓存：重跑时复用，省掉逐张重传（实测 535 张要 ~7 分钟）
CACHE = os.path.join(IMG_DIR, "_图片token缓存.json")
img_cache = load_json(CACHE, {})
if not isinstance(img_cache, dict):
    img_cache = {}
uploaded = 0

records, int_skip, int_ok, int_fail = [], 0, 0, 0

for it in data:
    link = (it.get(F_LINK) or "").strip()
    if SKIP_DUP and link in done:
        int_skip += 1
        continue

    fields = {}
    if link:
        fields[F_LINK] = {"link": link, "text": link} if LINK_AS_URL else link
    if it.get(F_TITLE):
        fields[F_TITLE] = it[F_TITLE]
    if it.get(F_SHOP):
        fields[F_SHOP] = it[F_SHOP]
    if it.get(F_TIME):
        fields[F_TIME] = int(it[F_TIME])          # 日期字段：毫秒时间戳

    if UPLOAD_IMG and it.get("本地图片") and os.path.exists(it["本地图片"]):
        tok = img_cache.get(link)
        if not tok:
            try:
                tok = upload_media(tk, it["本地图片"])
                img_cache[link] = tok
                uploaded += 1
            except Exception as e:
                xbot.app.logging.warning("图片上传失败，该条先不写图：%s" % str(e)[:120])
                tok = ""
        if tok:
            fields[F_IMG] = [{"file_token": tok}]

    if not fields:
        continue
    records.append({"fields": fields})
    done.add(link)

if uploaded:
    save_json(CACHE, img_cache)
xbot.app.logging.info("准备写入 %d 条（新传图片 %d 张，复用缓存 %d 张）"
                      % (len(records), uploaded, max(0, len(img_cache) - uploaded)))

if records:
    int_ok, int_fail = batch_create(tk, records)

xbot.app.logging.info("S4 写入成功 %d 条 / 失败 %d 条 / 跳过重复 %d 条" % (int_ok, int_fail, int_skip))
if int_fail and not int_ok:
    raise Exception(
        "S4：一条都没写进去。看清上面『批量写入失败』的 code —— "
        "1254068/URLFieldConvFail=链接字段格式不对；permission denied=应用没被加为表格协作者")
