# -*- coding: utf-8 -*-
# ============================================================================
# 影刀 · 插入代码段(Python) · S3
# 作用：清洗 + 去重 + 按天数过滤 + 按上新时间倒序
# 入参：lst_items（来自 S2）
# 出参：lst_final, int_drop
#
# 关于去重：这里按「产品链接」**全局去重**（店铺之间撞款也只留一条）。
# 关于天数：只保留最近 DAYS_LIMIT 天上新的；DAYS_LIMIT 在第 2 段定义，本段直接复用。
# ============================================================================

import xbot, os, re, json, datetime

# 店铺之间也可能撞款，这里按【产品链接】全局去重；想按标题去重就改下面这行
DEDUPE_BY = "link"          # link=按产品链接去重 | title=按产品标题去重
DROP_NO_TITLE = True        # 标题为空的丢掉
DROP_NO_TIME = False        # 改成 True 则丢掉没有上新时间的


def _item_id(it):
    if DEDUPE_BY == "title":
        return re.sub(r"\s+", "", (it.get(F_TITLE) or ""))[:80].lower()
    return (it.get(F_LINK) or "").strip()


# 入参兜底：前两段被禁用（只重跑「清洗+写入」）时，退回上次抓取落盘的 JSON
try:
    _src = lst_items
except NameError:
    _src = None
if not (_src and isinstance(_src, list)):
    try:
        IMG_DIR
    except NameError:
        IMG_DIR = r"D:\rpa_1688_img"
    for _d in (IMG_DIR, os.path.join(os.path.expanduser("~"), "rpa_1688_img_backup")):     # 两个位置都找
        try:
            with open(os.path.join(_d, "抓取结果.json"), encoding="utf-8") as _f:
                _src = json.load(_f)
            xbot.app.logging.warning("lst_items 为空，改用落盘的抓取结果 %d 条（%s）"
                                     % (len(_src), _d))
            break
        except Exception:
            continue
    if not (_src and isinstance(_src, list)):
        _src = []
        xbot.app.logging.warning("读不到落盘的抓取结果（IMG_DIR 和备份目录都没有），"
                                 "本段将输出 0 条 —— 请启用第 2 段完整抓取一遍")
src = _src if isinstance(_src, list) else []
uniq, seen, dropped = [], set(), 0

for it in src:
    if not isinstance(it, dict):
        dropped += 1
        continue
    if not (it.get(F_LINK) or "").startswith("http"):
        dropped += 1
        continue
    if DROP_NO_TITLE and not (it.get(F_TITLE) or "").strip():
        dropped += 1
        continue
    if DROP_NO_TIME and not it.get(F_TIME):
        dropped += 1
        continue
    key = _item_id(it)
    if not key or key in seen:
        dropped += 1
        continue
    seen.add(key)

    it[F_TITLE] = re.sub(r"\s+", " ", it[F_TITLE]).strip()
    it[F_SHOP] = (it.get(F_SHOP) or "").strip()
    # 本地图片丢了就把远程图清掉，避免上传报错
    if not (it.get("本地图片") and os.path.exists(it["本地图片"])):
        it["本地图片"] = ""
    uniq.append(it)

# ------------------- 按天数过滤（只要最近 N 天上新的） -------------------
try:
    DAYS_LIMIT
except NameError:
    DAYS_LIMIT = 7            # 单独跑本段时的兜底（正常由第 2 段定义）

if DAYS_LIMIT > 0:
    _today0 = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    CUT_MS = int((_today0 - datetime.timedelta(days=max(0, int(DAYS_LIMIT) - 1))).timestamp() * 1000)
    _before, _no_time, _kept = len(uniq), 0, []
    for it in uniq:
        t = it.get(F_TIME) or 0
        if not t:
            _no_time += 1
            dropped += 1
            continue
        if t >= CUT_MS:
            _kept.append(it)
        else:
            dropped += 1
    uniq = _kept
    xbot.app.logging.info(
        "按天数过滤：保留 %d 条（最近 %d 天，%s 00:00 起），丢弃 %d 条（其中 %d 条没有上新时间）"
        % (len(uniq), DAYS_LIMIT,
           datetime.datetime.fromtimestamp(CUT_MS / 1000).strftime("%Y-%m-%d"),
           _before - len(uniq), _no_time))
else:
    xbot.app.logging.info("不限天数：全部保留 %d 条" % len(uniq))

uniq.sort(key=lambda x: x.get(F_TIME) or 0, reverse=True)

lst_final = uniq
int_drop = dropped
xbot.app.logging.info("S3 清洗后 %d 条（丢弃/合并 %d 条）" % (len(lst_final), int_drop))
