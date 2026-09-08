# -*- coding: utf-8 -*-
"""
贵州银行类岗位投递追踪工作台 —— 官网数据每日同步脚本
=====================================================
职责：
  1. 抓取各银行官网招聘公告栏目（标准库 urllib，无第三方依赖）
  2. 提取公告条目（标题/日期/链接），维护各来源最近公告列表
  3. 对已跟踪的 2027 届校招条目更新"最后核验日期"
  4. 写回 workbench_data.json 并重新生成 workbench_data.js（工作台数据文件）
  5. 追加同步日志 sync_log.md，维护已见公告状态 sync_state.json

用法：
  python sync_bank_recruit.py            # 执行一次官网同步
  python sync_bank_recruit.py --probe    # 只探测各来源抓取结果，不写文件
  python sync_bank_recruit.py --init     # 首次初始化：从旧 HTML 提取数据并应用 2027 更新
  python sync_bank_recruit.py --rebuild  # 仅从 workbench_data.json 重新生成 workbench_data.js

说明：
  - SPA 站点（农业银行、贵阳银行等）无法用 urllib 直连解析，标记为"搜索补查"，
    由每日定时任务中的代理用网页抓取/搜索补全。
  - 全部只读公开招聘信息，不涉及登录态。
"""

import datetime
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(BASE, "workbench_data.json")
DATA_JS = os.path.join(BASE, "workbench_data.js")
STATE_JSON = os.path.join(BASE, "sync_state.json")
LOG_MD = os.path.join(BASE, "sync_log.md")
HTML_FILE = os.path.join(BASE, "bank_application_workbench.html")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 25
TODAY = datetime.date.today().isoformat()
NOW = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
# 兼容部分银行服务器的旧式 TLS 重协商
if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
    _CTX.options |= ssl.OP_LEGACY_SERVER_CONNECT

# ---------------------------------------------------------------- 来源配置
# mode: html = urllib 直连解析；agent = 需代理用网页抓取/搜索补查
SOURCES = [
    {"bank": "中国工商银行贵州省分行", "name": "工行人才招聘平台",
     "url": "https://job.icbc.com.cn/pc/index.html",
     "mode": "agent", "page": "校招公告", "relevance": ["2027", "校招", "校园招聘", "贵州", "秋招"]},
    {"bank": "中国建设银行贵州省分行", "name": "建行贵州机构校园招聘",
     "url": "https://job1.ccb.com/cn/job/org_index.html?orgId=2011629&planType=XY",
     "mode": "agent", "page": "机构公告与岗位", "relevance": ["2027", "校招", "校园招聘", "贵州", "秋招"]},
    {"bank": "中国银行贵州省分行", "name": "中国银行招聘公告",
     "url": "https://www.boc.cn/aboutboc/bi4/",
     "mode": "html", "page": "招聘公告", "relevance": ["2027", "校招", "校园招聘", "贵州", "秋招"]},
    {"bank": "中国农业银行贵州省分行", "name": "农行人才招聘(SPA)",
     "url": "https://career.abchina.com.cn",
     "mode": "agent", "page": "招聘公告", "relevance": ["2027", "校招", "校园招聘", "贵州", "秋招"]},
    {"bank": "交通银行贵州省分行", "name": "交行人才招聘(SPA)",
     "url": "https://job.bankcomm.com/index.do",
     "mode": "agent", "page": "招聘公告", "relevance": ["2027", "校招", "校园招聘", "贵州", "秋招"]},
    {"bank": "中国邮政储蓄银行贵州省分行", "name": "邮储校园招聘",
     "url": "https://www.psbc.com/cn/gyyc/rczp/xyzp/index.html",
     "mode": "html", "page": "校园招聘公告", "relevance": ["2027", "校招", "校园招聘", "贵州", "秋招"]},
    {"bank": "贵州银行", "name": "贵州银行门户人才招聘",
     "url": "https://www.bgzchina.com/article/category/c27",
     "mode": "html", "page": "人才招聘", "relevance": ["2027", "校招", "校园招聘", "秋招"]},
    {"bank": "贵阳银行", "name": "贵阳银行官网公告",
     "url": "https://www.bankgy.cn/",
     "mode": "agent", "page": "通知公告", "relevance": ["2027", "校招", "校园招聘", "秋招"]},
    {"bank": "贵州农商联合银行", "name": "贵州农商联合银行人才招聘",
     "url": "https://www.gznxbank.com/html/xn9999999/rczp/index.html",
     "mode": "html", "page": "人才招聘", "relevance": ["2027", "校招", "校园招聘", "秋招"]},
]

# 2027 届校招已确认事实更新（2026-09-08 首次同步时人工核验）
UPDATES_2027 = {
    "icbc-2027-guiyang": {
        "type": "2027届秋招（已发布）",
        "applyStart": "2026-09-04", "applyEnd": "2026-10-08",
        "written": "是",
        "writtenDetail": "笔试10月下旬；行测+英语+综合知识+专业科目，以站内通知为准",
        "degreeMatch": "本科可报；物联网工程与科技菁英岗匹配",
        "cohortMatch": "2026届可报（2026.1-2027.7毕业）",
        "risk": "低-中；科技菁英较匹配，轮岗以公告为准",
        "defaultNote": "贵州2027校招290人，9/4-10/8网申；岗位含星辰管培/专业英才/科技菁英/客户经理/客服经理。",
        "lastVerified": TODAY,
    },
    "icbc-2027-zunyi": {
        "type": "2027届秋招（已发布）",
        "applyStart": "2026-09-04", "applyEnd": "2026-10-08",
        "written": "是",
        "writtenDetail": "笔试10月下旬；以站内通知为准",
        "cohortMatch": "2026届可报（2026.1-2027.7毕业）",
        "risk": "中；地市分支以公告为准",
        "defaultNote": "贵州2027校招290人含遵义；公告发布后核对是否单列科技岗。",
        "lastVerified": TODAY,
    },
    "ccb-2027-guiyang": {
        "type": "2027届秋招（已发布）",
        "applyStart": "", "applyEnd": "2026-10-08",
        "role": "以岗位表为准：管培生/客户经理/客服经理等",
        "link": "https://job1.ccb.com/cn/job/org_index.html?orgId=2011629&planType=XY",
        "written": "是",
        "writtenDetail": "笔试10月底，以站内通知为准",
        "degreeMatch": "本科可报；科技类岗位以岗位表为准",
        "cohortMatch": "本科26-27届可报；管培生(境内)限2027年1-7月毕业",
        "risk": "中；客户/客服岗营销属性需区分，轮岗以公告为准",
        "defaultNote": "贵州2027校招170人，网申截止10/8；已见管培生(财务/法律/通用)、客户经理、客服经理，科技类岗位以岗位表为准。",
        "lastVerified": TODAY,
    },
    "boc-2027-guiyang": {
        "type": "2027届秋招（已发布）",
        "applyStart": "2026-09-03", "applyEnd": "2026-10-09",
        "role": "信息科技岗（省分行信息科技部）",
        "written": "是",
        "writtenDetail": "10月中下旬统一笔试，11月起面试",
        "degreeMatch": "本科可报；信息科技岗与物联网工程匹配",
        "cohortMatch": "2026届可报（2026.1.1-2027.7.31毕业）",
        "risk": "低-中；信息科技岗轮岗以公告为准",
        "defaultNote": "贵州2027校招30人，9/3-10/9网申；信息科技岗到省分行信息科技部工作。",
        "lastVerified": TODAY,
    },
    "abc-2027-guiyang": {
        "type": "2027届秋招（已发布）",
        "applyStart": "", "applyEnd": "2026-10-08",
        "link": "https://career.abchina.com/build/index.html#/NoticeDetails/110803628",
        "written": "是",
        "writtenDetail": "笔试10月下旬或11月上旬，以通知为准",
        "degreeMatch": "本科可报；信息科技岗面向计算机/软件工程等相关专业",
        "cohortMatch": "2026届可报（2026.1-2027.7毕业）",
        "risk": "低-中；科技岗轮岗以公告为准",
        "defaultNote": "贵州2027校招280人，网申截止10/8；最多可投两个岗位。",
        "lastVerified": TODAY,
    },
    "psbc-2027-guiyang": {
        "type": "2027届秋招（已发布）",
        "applyStart": "", "applyEnd": "2026-10-07",
        "link": "https://psbc2027.zhaopin.com",
        "written": "待通知",
        "writtenDetail": "笔试安排另行通知",
        "degreeMatch": "本科可报；信息科技岗部分要求硕士研究生，以岗位页为准",
        "cohortMatch": "2027届为主（境内2027.1-8毕业）；2026届届别风险高",
        "risk": "中；销售类岗位需剔除，信息科技岗以岗位页为准",
        "defaultNote": "贵州2027校招公告9/7发布，网申截止10/7；信息科技相关岗位以岗位页面学历要求为准。",
        "lastVerified": TODAY,
    },
    "bankcomm-2027-guiyang": {
        "type": "2027秋招（待公告）",
        "applyStart": "", "applyEnd": "",
        "cohortMatch": "2027届为主，接纳2026届未落实工作单位毕业生（以公告为准）",
        "defaultNote": "官网公告栏核验至2026-09-08，2027校招公告尚未发布，预计9月中上旬。",
        "lastVerified": TODAY,
    },
    "bgz-2027-guiyang": {
        "type": "2027秋招（待公告）",
        "defaultNote": "招聘官网job.bgzchina.com核验至2026-09-08，未见2027校招；2026金融科技岗位招聘仍在进行。",
        "lastVerified": TODAY,
    },
    "bankgy-2027-guiyang": {
        "type": "2027秋招（待公告）",
        "defaultNote": "贵阳银行校招通常在春季发布（2026校招3月发布230人）；2027届预计2027年春季，持续关注官网公告。",
        "lastVerified": TODAY,
    },
    "gznx-2027-guiyang": {
        "type": "2027秋招（待公告）",
        "defaultNote": "人才招聘栏核验至2026-09-08，未见2027校招；2026第二批社招流程收尾中。",
        "lastVerified": TODAY,
    },
}

DATE_RE = re.compile(r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})")
ANCHOR_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)
ANNO_KEYWORDS = ("招聘", "公告", "启事", "简章", "招录", "录用", "公示", "招募", "选调")

# agent 来源的已核验公告种子（2026-09-08 人工核验，供每日同步面板首屏展示）
SEED_ANNOUNCEMENTS = {
    "工行人才招聘平台": [
        {"title": "中国工商银行2027年度校园招聘启事（贵州分行290人，9/4-10/8）", "date": "2026-09-04",
         "url": "https://job.icbc.com.cn/pc/index.html#/main/school/announDetail/00000000000010659029"},
    ],
    "建行贵州机构校园招聘": [
        {"title": "中国建设银行贵州省分行2027年度校园招聘公告", "date": "2026-09-04",
         "url": "https://job1.ccb.com/cn/job/announcement.html?annoId=20260904082054721703"},
        {"title": "中国建设银行境内分支机构2027年度校园招聘公告", "date": "2026-09-04",
         "url": "https://job1.ccb.com/cn/job/announcement.html?annoId=20260901194840667699"},
    ],
    "农行人才招聘(SPA)": [
        {"title": "中国农业银行贵州省分行2027年度校园招聘公告（280人）", "date": "2026-09-04",
         "url": "https://career.abchina.com/build/index.html#/NoticeDetails/110803628"},
    ],
    "交行人才招聘(SPA)": [],
    "贵阳银行官网公告": [],
}


def fetch(url, tries=2):
    """抓取网页并解码，返回文本；失败抛异常。"""
    last_exc = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as resp:
                raw = resp.read()
            for enc in ("utf-8", "gb18030", "gbk"):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            import time
            time.sleep(2)
    raise last_exc


def clean_title(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def nearest_date(html, pos, max_gap=260):
    window = html[pos:pos + max_gap]
    m = DATE_RE.search(window)
    if not m:
        m2 = re.search(r"20\d{2}-\d{2}-\d{2}", window)
        return m2.group(0) if m2 else ""
    y, mo, d = m.groups()
    return "%04d-%02d-%02d" % (int(y), int(mo), int(d))


def url_path_date(url):
    """若 URL 路径含 YYYYMMDD 日期段（如中行/邮储公告页），优先采用；不含 query。"""
    path = urllib.parse.urlparse(url).path
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", path)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return "%s-%s-%s" % (y, mo, d)
    return ""


def extract_entries(html, url, relevance):
    """从公告列表页 HTML 提取 (title, date, href) 条目。"""
    entries = []
    for m in ANCHOR_RE.finditer(html):
        href, raw_title = m.group(1), m.group(2)
        title = clean_title(raw_title)
        if not (5 <= len(title) <= 90):
            continue
        if not any(k in title for k in ANNO_KEYWORDS):
            continue
        if title in ("更多", "查看更多", "首页", "上一页", "下一页") or "javascript" in href.lower():
            continue
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        abs_url = urllib.parse.urljoin(url, href)
        date = url_path_date(abs_url) or nearest_date(html, m.end())
        entries.append({"title": title, "date": date, "url": abs_url})
    # 去重（按标题+日期）
    seen, out = set(), []
    for e in entries:
        key = (e["title"], e["date"])
        if key in seen:
            continue
        seen.add(key)
        e["relevant"] = any(k in e["title"] for k in relevance)
        out.append(e)
    return out[:12]


def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def rebuild_js(data):
    """从 workbench_data.json 生成 workbench_data.js。"""
    opp = json.dumps(data.get("opportunities", []), ensure_ascii=False, indent=2)
    meta = json.dumps(data.get("meta", {}), ensure_ascii=False, indent=2)
    js = ("// 由 sync_bank_recruit.py 自动生成，请勿手工编辑\n"
          "window.OPPORTUNITIES = %s;\n\n"
          "window.WORKBENCH_META = %s;\n" % (opp, meta))
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write(js)


def init_from_html():
    """首次初始化：从旧 HTML 提取 opportunities 数组，应用 2027 更新，写 JSON/JS。"""
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const opportunities = \[(.*?)\n    \];", html, re.S)
    if not m:
        raise SystemExit("无法在 HTML 中定位 opportunities 数组")
    body = m.group(1)
    # JS 对象字面量 → JSON：为每行开头的键补引号
    body = re.sub(r'(?m)^\s*([A-Za-z_$][\w$]*)\s*:', r'"\1":', body)
    arr = json.loads("[" + body + "]")
    ids = [it.get("id") for it in arr]
    for it in arr:
        patch = UPDATES_2027.get(it.get("id"))
        if patch:
            it.update(patch)
    meta = {
        "verifiedDate": TODAY,
        "lastSync": NOW,
        "sources": [
            {"bank": s["bank"], "name": s["name"], "url": s["url"],
             "page": s["page"], "mode": s["mode"], "status": "pending",
             "fetchedAt": "", "announcements": SEED_ANNOUNCEMENTS.get(s["name"], [])}
            for s in SOURCES
        ],
    }
    data = {"opportunities": arr, "meta": meta, "schema": 2}
    save_json(DATA_JSON, data)
    rebuild_js(data)
    print("初始化完成：%d 条岗位，2027 更新 %d 条，验证日期 %s" % (len(arr), len(UPDATES_2027), TODAY))
    print("id 顺序：", ids)


def sync():
    data = load_json(DATA_JSON, None)
    if not data:
        raise SystemExit("缺少 workbench_data.json，请先运行 --init")
    state = load_json(STATE_JSON, {"seen": {}})
    seen = state.get("seen", {})
    meta = data.get("meta", {})
    sources = meta.get("sources", [])
    opp = data.get("opportunities", [])
    by_bank = {it["bank"]: it for it in opp}
    by_name = {s["name"]: s for s in sources}
    log_lines = ["## %s 官网同步" % NOW, ""]
    new_announcements = []

    for src in SOURCES:
        bank, name, url, mode = src["bank"], src["name"], src["url"], src["mode"]
        slot = by_name.get(name)
        if slot is None:
            slot = {"bank": bank, "name": name, "url": url, "page": src["page"],
                    "mode": mode, "status": "pending", "fetchedAt": "", "announcements": []}
            sources.append(slot)
            by_name[name] = slot
        slot["bank"], slot["url"], slot["page"], slot["mode"] = bank, url, src["page"], mode
        slot["fetchedAt"] = NOW

        if mode == "agent":
            slot["status"] = "agent"
            log_lines.append("- %s：SPA 站点，需搜索补查（保留上次列表）" % bank)
            cur = by_bank.get(bank)
            if cur and "2027" in cur.get("type", ""):
                cur["lastVerified"] = TODAY
            continue

        try:
            html = fetch(url)
        except Exception as exc:
            slot["status"] = "fail"
            log_lines.append("- %s：抓取失败：%s" % (bank, exc))
            continue

        entries = extract_entries(html, url, src["relevance"])
        slot["announcements"] = entries
        slot["status"] = "ok" if entries else "ok-empty"
        log_lines.append("- %s：抓取 %d 条公告（%s）" % (bank, len(entries), url))

        # 检测新公告（相对上次已见；仅近 45 天内发布的算"新"）
        cutoff = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        old = set(seen.get(name, []))
        for e in entries:
            if e["title"] in old or not e.get("relevant"):
                continue
            if e["date"] and e["date"] < cutoff:
                continue
            new_announcements.append({"bank": bank, "title": e["title"],
                                      "date": e["date"], "url": e["url"]})
        seen[name] = [e["title"] for e in entries]

        # 来源抓取成功 → 更新对应 2027 条目的核验日期
        cur = by_bank.get(bank)
        if cur and "2027" in cur.get("type", ""):
            cur["lastVerified"] = TODAY

    meta["verifiedDate"] = TODAY
    meta["lastSync"] = NOW

    save_json(DATA_JSON, data)
    save_json(STATE_JSON, {"seen": seen})
    rebuild_js(data)

    if new_announcements:
        log_lines.append("")
        log_lines.append("**发现新公告（相关）：**")
        for na in new_announcements:
            log_lines.append("- [%s] %s（%s）%s" % (na["bank"], na["title"], na["date"], na["url"]))
    log_lines.append("")
    with open(LOG_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print("同步完成 %s：%d 个来源，新相关公告 %d 条" % (NOW, len(sources), len(new_announcements)))
    for na in new_announcements:
        print("  新公告：%s - %s (%s)" % (na["bank"], na["title"], na["date"]))
    for s in sources:
        print("  [%s] %s (%s)" % (s.get("status"), s["bank"], s.get("fetchedAt", "")))


def probe():
    for src in SOURCES:
        if src["mode"] == "agent":
            print("\n=== %s（agent，跳过直连）===" % src["bank"])
            continue
        print("\n=== %s | %s ===" % (src["bank"], src["url"]))
        try:
            html = fetch(src["url"])
        except Exception as exc:
            print("  抓取失败：%s" % exc)
            continue
        entries = extract_entries(html, src["url"], src["relevance"])
        for e in entries:
            flag = "★" if e.get("relevant") else " "
            print("  %s [%s] %s %s" % (flag, e["date"], e["title"], e["url"][:90]))
        if not entries:
            print("  （未解析到公告条目）")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--init":
        init_from_html()
    elif arg == "--probe":
        probe()
    elif arg == "--rebuild":
        d = load_json(DATA_JSON, None)
        if not d:
            raise SystemExit("缺少 workbench_data.json")
        rebuild_js(d)
        print("已重新生成 workbench_data.js")
    else:
        sync()
