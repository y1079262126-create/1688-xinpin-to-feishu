# -*- coding: utf-8 -*-
# ============================================================================
# 影刀 · 插入代码段(Python) · S1
# 作用：读取多维表格「供应商信息表（调用）」→ 输出 lst_targets
# 出参：lst_targets = [{"供应商名称": "...", "网址": "https://xxx.1688.com/page/newofferlist.htm"}, ...]
# ============================================================================

# ========================= 总配置区（整个流程只在这里改） =========================
# ---- 飞书自建应用（要加为下面两张表的协作者·可编辑）----
APP_ID     = "cli_填入你的AppID"
APP_SECRET = "填入你的AppSecret"
# ---- 多维表格 ----
BASE_TOKEN = "K1gEbvSPkanqEqsqV2tcWw70nsd"
TBL_SRC    = "tbl5FsegbR2RRvcK"   # 源表：供应商信息表（调用）
TBL_DST    = "tbl3BzPbvQ66WjGV"   # 目标表：新品上新（下载）
# ---- 目标表的字段名（换了表格/改了字段名，只改这一行）----
F_LINK, F_TITLE, F_TIME, F_SHOP, F_IMG = ("产品链接", "产品标题", "上新时间", "供应商名称", "产品图片")
# ---- 源表的字段名 ----
SRC_F_NAME, SRC_F_URL = "供应商名称", "新品上新网址"
# ================================================================================

import xbot, json, urllib.request


def _http(url, method="GET", token=None, payload=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def feishu_token():
    """换取 tenant_access_token（有效期 2 小时，每次运行现取即可）"""
    d = _http("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
              "POST", payload={"app_id": APP_ID, "app_secret": APP_SECRET})
    if d.get("code") != 0:
        raise Exception("飞书鉴权失败：%s" % d)
    return d["tenant_access_token"]


def txt(v):
    """飞书文本字段可能是 str / list / {"link","text"}，统一取成字符串"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return (v.get("link") or v.get("text") or "").strip()
    if isinstance(v, list):
        return "".join(txt(x) for x in v).strip()
    return str(v).strip()


def read_suppliers():
    tk = feishu_token()
    out, page_token = [], None
    while True:
        url = ("https://open.feishu.cn/open-apis/bitable/v1/apps/%s/tables/%s/records?page_size=500"
               % (BASE_TOKEN, TBL_SRC))
        if page_token:
            url += "&page_token=" + page_token
        d = _http(url, "GET", tk)
        if d.get("code") != 0:
            raise Exception("读取供应商表失败：%s" % d)
        dd = d["data"]
        for it in dd.get("items") or []:
            f = it.get("fields") or {}
            site = txt(f.get(SRC_F_URL))
            name = txt(f.get(SRC_F_NAME))
            if site.startswith("http"):
                out.append({SRC_F_NAME: name or site, "网址": site})
        if dd.get("has_more") and dd.get("page_token"):
            page_token = dd["page_token"]
        else:
            return out


# ---------------------------------- 执行 ----------------------------------
lst_targets = read_suppliers()
xbot.app.logging.info("S1 读取供应商 %d 家" % len(lst_targets))
if not lst_targets:
    raise Exception("S1：没有读到任何「新品上新网址」，请检查配置与表格权限")
