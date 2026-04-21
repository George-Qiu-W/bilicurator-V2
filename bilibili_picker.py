"""
B站优质视频筛选工具 v3.0
- Web模式: 浏览器内自定义标签、数量、时间范围，实时刷新
- 使用B站API获取发布时间，精确筛选
- 封面图anti-hotlink修复
- 修复Windows编码问题
"""

import subprocess
import json
import sys
import os
import re
import argparse
import io
import time
import urllib.request
import urllib.parse
import urllib.error
import threading
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# ========== Windows编码修复 ==========
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    # 关键：设置环境变量，让bili子进程也用UTF-8
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'

# ========== 常量 ==========
DEFAULT_TAGS = ["体育", "健康", "运动", "心理学", "AI coding", "科技", "编程"]

TAG_PRESETS = {
    "科技数码": ["AI coding", "科技", "编程", "人工智能", "机器学习"],
    "生活健康": ["健康", "运动", "心理学", "健身", "营养学"],
    "知识教育": ["教育", "历史", "数学", "物理", "哲学"],
    "娱乐休闲": ["音乐", "游戏", "电影", "动漫", "美食"],
}

TAG_ICONS = {
    "体育": "⚽", "健康": "💚", "运动": "🏃", "心理学": "🧠",
    "AI coding": "🤖", "科技": "🔬", "编程": "💻", "设计": "🎨",
    "音乐": "🎵", "游戏": "🎮", "美食": "🍜", "旅行": "✈️",
    "教育": "📚", "金融": "💰", "历史": "📜", "自然": "🌿",
    "摄影": "📷", "电影": "🎬", "动漫": "🎞️", "汽车": "🚗",
    "数学": "📐", "物理": "⚛️", "商业": "💼", "哲学": "💭",
    "文学": "📝", "艺术": "🎭", "舞蹈": "💃", "健身": "🏋️",
    "人工智能": "🧠", "机器学习": "📊", "深度学习": "🔮",
}

CONFIG_DIR = Path.home() / ".bilibili_picker"
CONFIG_FILE = CONFIG_DIR / "config.json"

# ========== 工具函数 ==========

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def esc(s):
    """HTML转义"""
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def fmt_count(n):
    if not n:
        return "0"
    n = int(n)
    if n >= 100000000:
        return f"{n/100000000:.1f}亿"
    if n >= 10000:
        return f"{n/10000:.1f}万"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def fmt_date(dt):
    if not dt:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%m-%d %H:%M")
    return str(dt)


def days_ago_text(dt):
    if not dt or not isinstance(dt, datetime):
        return ""
    d = (datetime.now() - dt).days
    if d == 0:
        return "今天"
    if d == 1:
        return "昨天"
    if d < 7:
        return f"{d}天前"
    if d < 30:
        return f"{d//7}周前"
    return f"{d//30}个月前"


# ========== B站搜索 & API ==========

def _to_int(val):
    """安全地将值转换为整数（处理字符串、None、MM:SS格式等情况）"""
    if val is None:
        return 0
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        # 处理 MM:SS 或 H:MM:SS 格式
        if ':' in val:
            parts = val.split(':')
            try:
                if len(parts) == 2:  # MM:SS
                    return int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:  # H:MM:SS
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except (ValueError, IndexError):
                pass
        # 普通数字字符串
        try:
            return int(val)
        except ValueError:
            return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0




def search_videos(keyword, max_results=60, max_pages=4):
    """
    搜索B站视频，直接调用B站公开搜索API（无需 bili CLI）。
    接口：https://api.bilibili.com/x/web-interface/search/type
    """
    all_videos = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    for page in range(1, max_pages + 1):
        if len(all_videos) >= max_results:
            break
        params = urllib.parse.urlencode({
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": 20,
            "order": "totalrank",
        })
        url = f"https://api.bilibili.com/x/web-interface/search/type?{params}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") != 0:
                print(f"    [WARN] B站搜索API返回 code={data.get('code')}, msg={data.get('message','')}")
                break
            results = data.get("data", {}).get("result", [])
            if not results:
                break
            for item in results:
                bvid = item.get("bvid", "")
                if not bvid:
                    continue
                # 统一字段名，兼容后续 enrich 逻辑
                all_videos.append({
                    "bvid": bvid,
                    "title": re.sub(r"<[^>]+>", "", item.get("title", "")),  # 去除HTML标签
                    "author": item.get("author", ""),
                    "mid": item.get("mid", 0),
                    "play": _to_int(item.get("play", 0)),
                    "danmaku": _to_int(item.get("danmaku", 0)),
                    "cover": item.get("pic", "").lstrip("//"),
                    "duration": item.get("duration", ""),
                    "desc": item.get("description", ""),
                    "tag": keyword,
                })
        except Exception as e:
            print(f"    [WARN] B站搜索API异常: {e}")
            break
        time.sleep(0.5)

    return all_videos


def get_video_info(bvid):
    """
    通过B站公开API获取视频详细信息（pubdate、封面、统计数据）。
    无需登录。
    """
    if not bvid or not bvid.startswith("BV"):
        return None
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                info = data.get("data", {})
                return {
                    "pubdate": datetime.fromtimestamp(info.get("pubdate", 0)) if info.get("pubdate") else None,
                    "pubdate_ts": info.get("pubdate", 0),
                    "cover": info.get("pic", ""),
                    "view": info.get("stat", {}).get("view", 0),
                    "danmaku": info.get("stat", {}).get("danmaku", 0),
                    "like": info.get("stat", {}).get("like", 0),
                    "coin": info.get("stat", {}).get("coin", 0),
                    "favorite": info.get("stat", {}).get("favorite", 0),
                    "share": info.get("stat", {}).get("share", 0),
                    "duration": info.get("duration", 0),
                    "desc": info.get("desc", ""),
                }
    except Exception:
        pass
    return None


def enrich_videos(videos, days_limit=None, silent=False):
    """
    并发获取视频发布时间和封面。
    days_limit: 只保留最近N天内的视频。
    使用线程池并发请求B站API，大幅减少等待时间。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 过滤有效bvid
    valid = [(i, v) for i, v in enumerate(videos) if v.get("bvid", "").startswith("BV")]
    if not valid:
        return []

    enriched_list = []
    failed_indices = []

    # 并发获取（限制并发数避免触发B站反爬）
    max_workers = min(5, len(valid))
    cutoff = datetime.now() - timedelta(days=days_limit) if days_limit else None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {}
        for idx, v in valid:
            future = pool.submit(get_video_info, v["bvid"])
            future_map[future] = (idx, v)

        for future in as_completed(future_map):
            idx, v = future_map[future]
            try:
                info = future.result()
                if info:
                    # 时间筛选
                    if cutoff and info["pubdate"] and info["pubdate"] < cutoff:
                        continue

                    merged = dict(v)
                    merged["pubdate"] = info["pubdate"]
                    merged["cover"] = info["cover"] or merged.get("cover", "")
                    merged["view"] = info["view"] or merged.get("play", 0)
                    merged["danmaku"] = info["danmaku"]
                    merged["like"] = info["like"]
                    merged["coin"] = info["coin"]
                    merged["favorite"] = info["favorite"]
                    merged["share"] = info["share"]
                    enriched_list.append(merged)
                else:
                    failed_indices.append(idx)
            except Exception:
                failed_indices.append(idx)

    # API失败的：不限时间则保留基本信息
    if not days_limit:
        for idx in failed_indices:
            enriched_list.append(videos[idx])

    return enriched_list


# ========== 擦边/低质内容过滤规则 ==========

# 标题党/擦边关键词（大小写不敏感）
LOW_QUALITY_KEYWORDS = [
    # 擦边暗示（扣分而非直接过滤）
    "擦边", "性感", "诱惑", "妩媚", "妖娆", "火辣", "劲爆",
    "美女", "小姐姐", "妹妹", "女神",  # 从BLOCK移到扣分
    "不看后悔", "深夜", "私密", "禁播", "删减", "未删减",
    "大尺度", "尺度", "暴露", "走光", "偷拍",
    # 标题党
    "震惊", "惊呆了", "傻眼", "炸裂", "离谱", "逆天",
    "全网", "首发", "独家", "绝密", "内部", "泄露",
    "点击", "观看", "必看", "速看", "抓紧", "马上",
    # 诱导点击
    "最后", "结局", "结果", "居然", "竟然", "没想到",
    "原来", "真相", "揭秘", "曝光", "实锤", "石锤",
]

# 严重擦边词（直接过滤）
BLOCK_KEYWORDS = [
    "裸", "胸", "腿", "丝袜", "黑丝", "白丝", "jk", "制服",
    # 放宽："美女", "小姐姐" 等词在B站太常见，移到 LOW_QUALITY_KEYWORDS 扣分而非直接过滤
    "纯欲", "甜欲", "诱惑", "妩媚", "妖娆",
]


def is_low_quality_content(v):
    """
    检测是否为低质/擦边内容
    返回: (是否低质, 原因)
    """
    title = v.get("title", "").lower()
    play = max(_to_int(v.get("view") or v.get("play")), 1)
    like = _to_int(v.get("like"))
    coin = _to_int(v.get("coin"))
    duration = _to_int(v.get("duration"))

    # 1. 严重擦边词直接过滤
    for kw in BLOCK_KEYWORDS:
        if kw in title:
            return True, f"含敏感词: {kw}"

    # 2. 标题党关键词扣分
    title_deduction = 0
    for kw in LOW_QUALITY_KEYWORDS:
        if kw in title:
            title_deduction += 15  # 每个词扣15分

    # 3. 互动率过低（高播放低互动=封面党）- 放宽门槛
    like_rate = like / play * 100
    coin_rate = coin / play * 100

    if play > 100000:  # 只针对超高播放视频
        if like_rate < 0.3:  # 点赞率<0.3%（放宽）
            return True, f"高播低互动(点赞率{like_rate:.2f}%)"
        if coin_rate < 0.01:  # 投币率<0.01%（放宽）
            return True, f"高播低认可(投币率{coin_rate:.3f}%)"

    # 4. 短时长 + 超高播放 = 疑似营销/搬运
    if duration < 30 and play > 500000:  # 更严格的条件
        return True, "短视频高播放(疑似营销)"

    # 5. 标题过长（堆砌关键词）- 放宽到60字符
    if len(title) > 60:
        return True, "标题过长(堆砌关键词)"

    return False, None


def calculate_quality_score(v):
    """
    计算视频综合质量评分（0-100分）
    多维度评估，避免单纯依赖播放量被标题党/擦边内容钻空子
    """
    play = max(_to_int(v.get("view") or v.get("play")), 1)  # 避免除0
    like = _to_int(v.get("like"))
    coin = _to_int(v.get("coin"))
    fav = _to_int(v.get("favorite"))
    danmaku = _to_int(v.get("danmaku"))
    share = _to_int(v.get("share"))
    duration = _to_int(v.get("duration"))
    pubdate = v.get("pubdate")
    title = v.get("title", "").lower()

    # 1. 互动深度分 (0-35分) - 核心指标
    # 点赞率: 正常视频 2-8%，优质可达 10%+
    like_rate = like / play * 100
    like_score = min(like_rate * 3, 15)  # 5%点赞率=15分

    # 投币率: 比点赞更稀缺，权重更高
    coin_rate = coin / play * 100
    coin_score = min(coin_rate * 10, 12)  # 1.2%投币率=12分

    # 收藏率: 内容有价值才会收藏
    fav_rate = fav / play * 100
    fav_score = min(fav_rate * 8, 8)  # 1%收藏率=8分

    interaction_score = like_score + coin_score + fav_score

    # 2. 社区认可分 (0-25分)
    # 弹幕密度: 每分钟弹幕数反映讨论热度
    if duration > 0:
        danmaku_per_min = danmaku / (duration / 60)
    else:
        danmaku_per_min = 0
    danmaku_score = min(danmaku_per_min * 2, 15)  # 7.5条/分钟=15分

    # 分享率: 愿意分享=内容有价值
    share_rate = share / play * 100
    share_score = min(share_rate * 5, 10)  # 2%分享率=10分

    community_score = danmaku_score + share_score

    # 3. 完播潜力分 (0-15分) - 时长适中
    # 3-15分钟是知识类/干货类视频的黄金时长
    if 180 <= duration <= 900:  # 3-15分钟
        duration_score = 15
    elif duration < 60:  # 太短可能没深度
        duration_score = 5
    elif duration > 1800:  # 超过30分钟，完播率低
        duration_score = 8
    else:
        duration_score = 12

    # 4. 新鲜度分 (0-15分)
    if pubdate and isinstance(pubdate, datetime):
        days_old = (datetime.now() - pubdate).days
        if days_old <= 3:
            fresh_score = 15
        elif days_old <= 7:
            fresh_score = 12
        elif days_old <= 14:
            fresh_score = 9
        elif days_old <= 30:
            fresh_score = 6
        else:
            fresh_score = 3
    else:
        fresh_score = 5

    # 5. 基础热度分 (0-10分) - 播放量对数衰减，避免大V垄断
    # 使用对数让中小UP也有机会
    import math
    if play >= 1000000:
        heat_score = 10
    elif play >= 100000:
        heat_score = 8 + math.log10(play / 100000) * 2
    elif play >= 10000:
        heat_score = 5 + math.log10(play / 10000) * 3
    elif play >= 1000:
        heat_score = 2 + math.log10(play / 1000) * 3
    else:
        heat_score = play / 500
    heat_score = min(heat_score, 10)

    total_score = interaction_score + community_score + duration_score + fresh_score + heat_score

    # 6. 标题党扣分
    title_deduction = 0
    for kw in LOW_QUALITY_KEYWORDS:
        if kw in title:
            title_deduction += 15
    total_score -= min(title_deduction, 50)  # 最多扣50分

    return round(max(total_score, 0), 1)


def get_quality_level(score):
    """根据分数返回质量等级和样式类"""
    if score >= 85:
        return "🏆 神作", "q-god", "#ffd700"
    elif score >= 70:
        return "⭐ 优质", "q-excellent", "#00d67e"
    elif score >= 55:
        return "👍 良好", "q-good", "#00a1d6"
    elif score >= 40:
        return "📺 一般", "q-normal", "#8b949e"
    else:
        return "💤 冷门", "q-low", "#6e7681"


def filter_and_rank(videos, min_play=0, sort_by_quality=True, strict_mode=True, enriched=False):
    """
    去重、筛选、按质量评分排序
    sort_by_quality: True=按质量分排序, False=按播放量排序
    strict_mode: True=启用擦边/低质内容过滤
    enriched: True=视频已enrich（有完整数据），False=仅基础数据（bili搜索结果）
    """
    seen = set()
    filtered = []
    blocked_count = 0

    for v in videos:
        bvid = v.get("bvid", "")
        title = v.get("title", "")
        play = _to_int(v.get("play") or v.get("view"))

        if not bvid or not title or bvid in seen:
            continue
        seen.add(bvid)

        if play < min_play:
            continue

        # 严格模式：过滤擦边/低质内容（仅在enrich后执行，因为需要完整数据）
        if strict_mode and enriched:
            is_low, reason = is_low_quality_content(v)
            if is_low:
                v["blocked"] = True
                v["block_reason"] = reason
                blocked_count += 1
                continue  # 直接跳过不加入结果

        # 计算质量分并附加到视频数据
        v["quality_score"] = calculate_quality_score(v)

        # 质量分太低的也过滤掉（仅在enrich后执行）- 门槛降低到15分，避免过度过滤
        if strict_mode and enriched and v["quality_score"] < 15:
            continue

        filtered.append(v)

    if sort_by_quality:
        # 按质量分排序（质量分相同时按播放量）
        filtered.sort(key=lambda x: (float(x.get("quality_score", 0) or 0), int(x.get("view", 0) or x.get("play", 0) or 0)), reverse=True)
    else:
        # 传统播放量排序
        filtered.sort(key=lambda x: int(x.get("play", 0) or x.get("view", 0) or 0), reverse=True)

    return filtered


# ========== 核心搜索流程 ==========

def do_search(tags, number, days_limit, min_play=500, silent=False, sort_by_quality=True):
    """
    完整的搜索→筛选→enrich→质量评分流程。
    返回 { tag: [video_dict, ...] }
    sort_by_quality: True=按质量分排序, False=按播放量排序
    """
    videos_by_tag = {}

    for tag in tags:
        if not silent:
            print(f"\n🔍 搜索: #{tag}")

        # 1. bili搜索
        raw = search_videos(tag, max_results=80)
        if not raw:
            if not silent:
                print(f"  ⚠ 无结果")
            continue

        if not silent:
            print(f"  📊 搜索到 {len(raw)} 个结果")

        # 2. 去重 + 基础筛选（先不按质量排序，等enrich后再算质量分）
        filtered = filter_and_rank(raw, min_play=min_play, sort_by_quality=False)
        if not filtered:
            # 降低门槛再试
            filtered = filter_and_rank(raw, min_play=0, sort_by_quality=False)
            if not filtered:
                if not silent:
                    print(f"  ⚠ 无有效视频")
                continue

        # 3. 取候选集，获取详细信息
        target_count = min(number * 3, len(filtered))
        candidates = filtered[:target_count]

        if not silent:
            print(f"  📡 获取 {len(candidates)} 个视频的详细信息...")

        enriched = enrich_videos(candidates, days_limit=days_limit, silent=silent)

        # 4. 如果时间筛选后为空，放宽时间限制再试一次
        if not enriched and days_limit:
            if not silent:
                print(f"  ⚠ 严格时间筛选无结果，放宽限制...")
            enriched = enrich_videos(filtered[:number], days_limit=None, silent=True)

        if not enriched:
            if not silent:
                print(f"  ⚠ 最终无可用视频")
            continue

        # 5. enrich后重新进行严格过滤（此时有完整数据）
        # 策略：先尝试严格过滤，如果不够N个，逐步放宽直到凑够
        filtered_strict = filter_and_rank(enriched, min_play=0, sort_by_quality=sort_by_quality, strict_mode=True, enriched=True)
        
        if len(filtered_strict) >= number:
            # 严格过滤后够数，直接使用
            videos_by_tag[tag] = filtered_strict[:number]
            filter_mode = "严格过滤"
        else:
            # 严格过滤后不够，尝试宽松过滤（去掉strict_mode）
            filtered_loose = filter_and_rank(enriched, min_play=0, sort_by_quality=sort_by_quality, strict_mode=False, enriched=True)
            if len(filtered_loose) >= number:
                videos_by_tag[tag] = filtered_loose[:number]
                filter_mode = "宽松过滤"
            else:
                # 宽松过滤还不够，直接按质量分排序取前N个（不过滤）
                enriched.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
                videos_by_tag[tag] = enriched[:number]
                filter_mode = "质量分排序"
        
        if not silent:
            avg_score = sum(v.get("quality_score", 0) for v in videos_by_tag[tag]) / len(videos_by_tag[tag]) if videos_by_tag[tag] else 0
            print(f"  ✅ [{filter_mode}] 筛选出 {len(videos_by_tag[tag])} 个视频 (平均质量分: {avg_score:.1f})")

    return videos_by_tag


# ========== HTML生成 ==========

def build_card_html(v, rank, tag):
    """生成单个视频卡片HTML（带质量评分）"""
    bvid = v.get("bvid", "")
    title = esc(v.get("title", "未知标题"))
    author = esc(v.get("author", "未知UP主"))
    play = _to_int(v.get("play") or v.get("view"))
    like = _to_int(v.get("like"))
    coin = _to_int(v.get("coin"))
    fav = _to_int(v.get("favorite"))
    danmaku = _to_int(v.get("danmaku"))
    duration = v.get("duration", "")
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else "#"
    pubdate = v.get("pubdate")

    # 质量评分
    quality_score = v.get("quality_score", 0)
    q_label, q_class, q_color = get_quality_level(quality_score)

    # 计算各维度指标用于展示
    like_rate = (like / max(play, 1)) * 100
    coin_rate = (coin / max(play, 1)) * 100
    fav_rate = (fav / max(play, 1)) * 100

    # 封面图
    cover = v.get("cover", "")
    if cover:
        if cover.startswith("//"):
            cover = "https:" + cover
        img = f'<img src="{esc(cover)}" alt="" class="cover-img" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'" />'
        placeholder_style = "display:none"
    else:
        img = ""
        placeholder_style = "display:flex"

    # 互动率标签
    hot_tag = ""
    if play > 0:
        ir = ((like + coin * 2 + fav) / play) * 100
        if ir > 15:
            hot_tag = "💬 高互动"
        elif ir > 8:
            hot_tag = "👏 好评"

    dur_badge = f'<span class="dur">{esc(duration)}</span>' if duration else ""
    pub_badge = f'<span class="pub">📅 {esc(days_ago_text(pubdate))} · {esc(fmt_date(pubdate))}</span>' if pubdate else ""

    # 质量分数条
    score_bar_width = int(quality_score)

    return f"""<div class="card" onclick="window.open('{url}','_blank')">
  <div class="card-rank">#{rank}</div>
  <div class="card-q {q_class}">{q_label}</div>
  <div class="card-score" title="质量评分: {quality_score}/100\n点赞率: {like_rate:.1f}% | 投币率: {coin_rate:.2f}% | 收藏率: {fav_rate:.2f}%">
    <div class="score-bar" style="width:{score_bar_width}%;background:{q_color}"></div>
    <span class="score-text">{quality_score}</span>
  </div>
  {"<div class='card-hot'>" + esc(hot_tag) + "</div>" if hot_tag else ""}
  <div class="card-cover">
    {img}
    <div class="card-ph" style="{placeholder_style}">
      <svg viewBox="0 0 160 90" fill="none"><rect width="160" height="90" rx="8" fill="#1a1a2e"/><circle cx="80" cy="40" r="20" fill="#e94560" opacity=".6"/><polygon points="74,30 74,50 90,40" fill="#fff" opacity=".8"/><text x="80" y="72" text-anchor="middle" fill="#666" font-size="10" font-family="monospace">{esc(duration) if duration else 'B站'}</text></svg>
    </div>
    {dur_badge}
  </div>
  <div class="card-body">
    <h3 class="card-title">{title}</h3>
    <div class="card-meta"><span class="card-au">👤 {author}</span><span class="card-pv">▶ {fmt_count(play)}播放</span></div>
    <div class="card-stats">
      <span>❤️ {fmt_count(like)}</span><span>🪙 {fmt_count(coin)}</span><span>⭐ {fmt_count(fav)}</span><span>💬 {fmt_count(danmaku)}</span>
    </div>
    {pub_badge}
    <a href="{url}" target="_blank" rel="noopener" class="card-link" onclick="event.stopPropagation()">在B站观看 →</a>
  </div>
</div>"""


def build_sections(videos_by_tag):
    """从videos_by_tag构建HTML片段"""
    cards_html = ""
    nav_html = ""
    total = 0

    for tag, videos in videos_by_tag.items():
        if not videos:
            continue
        total += len(videos)
        icon = TAG_ICONS.get(tag, "📌")
        tag_id = tag.replace(" ", "-").replace("#", "").lower()
        nav_html += f'<a href="#tag-{tag_id}" class="nav-item">{icon} {esc(tag)} ({len(videos)})</a>\n'

        cards_html += f"""<section class="tag-section" id="tag-{tag_id}">
  <div class="tag-hdr">
    <h2 class="tag-title"><span class="tag-icon">{icon}</span> #{esc(tag)}</h2>
    <span class="tag-cnt">{len(videos)} 个视频</span>
  </div>
  <div class="grid">
"""
        for i, v in enumerate(videos):
            cards_html += build_card_html(v, i + 1, tag)
        cards_html += "</div></section>\n"

    return cards_html, nav_html, total, len(videos_by_tag)


# ========== CSS (内联) ==========

CSS = """*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg0:#0d1117;--bg1:#161b22;--bg2:#1c2333;--bg2h:#242d3d;
  --t1:#e6edf3;--t2:#8b949e;--t3:#6e7681;
  --pink:#fb7299;--blue:#00a1d6;--green:#00d67e;--orange:#ff6b35;--purple:#a855f7;
  --border:#30363d;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC','PingFang SC',sans-serif;background:var(--bg0);color:var(--t1);min-height:100vh;line-height:1.6}

/* Hero */
.hero{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);padding:40px 20px 30px;text-align:center;position:relative;overflow:hidden}
.hero::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 20% 40%,rgba(251,114,153,.08) 0%,transparent 40%),radial-gradient(circle at 80% 60%,rgba(0,161,214,.08) 0%,transparent 40%);animation:fl 20s ease-in-out infinite}
@keyframes fl{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
.hero-content{position:relative;z-index:1}
.hero h1{font-size:2.4em;font-weight:800;background:linear-gradient(135deg,var(--pink),var(--blue),var(--purple));background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:10px;animation:sh 3s ease-in-out infinite alternate}
@keyframes sh{0%{background-position:0% center}100%{background-position:100% center}}
.hero .sub{color:var(--t2);font-size:1.05em;max-width:600px;margin:0 auto}
.hero .stats{margin-top:18px;display:flex;justify-content:center;gap:14px;flex-wrap:wrap}
.hero .si{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);padding:6px 16px;border-radius:24px;font-size:.85em;color:var(--t2)}
.hero .si strong{color:var(--pink);font-weight:700}

/* 配置面板 */
.cfg{background:var(--bg1);border-bottom:1px solid var(--border);padding:14px 20px}
.cfg-inner{max-width:1200px;margin:0 auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.cfg label{font-size:.8em;color:var(--t2);font-weight:600;white-space:nowrap}
.cfg input[type="text"],.cfg input[type="number"],.cfg select{background:var(--bg0);color:var(--t1);border:1px solid var(--border);border-radius:8px;padding:7px 12px;font-size:.85em;outline:none;transition:border-color .2s}
.cfg input:focus,.cfg select:focus{border-color:var(--pink);box-shadow:0 0 0 2px rgba(251,114,153,.15)}
.cfg .ti{flex:1;min-width:200px;max-width:500px}
.cfg .ni{width:70px;text-align:center}
.cfg .di{width:130px}
.btn{background:linear-gradient(135deg,var(--pink),var(--blue));color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:.85em;font-weight:700;cursor:pointer;transition:all .2s;display:inline-flex;align-items:center;gap:6px}
.btn:hover{transform:translateY(-1px);box-shadow:0 4px 15px rgba(251,114,153,.4)}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.btn .sp{display:none;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}
.btn.loading .sp{display:inline-block}
.btn.loading .bt{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.presets{display:flex;gap:6px;flex-wrap:wrap;max-width:1200px;margin:10px auto 0;padding:0 20px}
.pt{background:rgba(255,255,255,.04);border:1px solid var(--border);color:var(--t3);padding:3px 10px;border-radius:12px;font-size:.72em;cursor:pointer;transition:all .2s;user-select:none}
.pt:hover{border-color:var(--pink);color:var(--pink);background:rgba(251,114,153,.08)}

/* 状态栏 */
.sbar{text-align:center;padding:8px;font-size:.82em;color:var(--t3);display:none}
.sbar.show{display:block}
.sbar.loading{color:var(--blue)}
.sbar.error{color:var(--orange)}
.sbar.ok{color:var(--green)}

/* 导航 */
.nav{background:rgba(22,27,34,.95);padding:12px 20px;position:sticky;top:0;z-index:100;border-bottom:1px solid var(--border);backdrop-filter:blur(12px)}
.nav-inner{max-width:1200px;margin:0 auto;display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.nav-inner::-webkit-scrollbar{height:3px}
.nav-inner::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.nav-item{text-decoration:none;color:var(--t2);padding:5px 14px;border-radius:20px;white-space:nowrap;font-size:.82em;transition:all .25s;background:rgba(255,255,255,.03);border:1px solid transparent;font-weight:500}
.nav-item:hover{color:var(--pink);background:rgba(251,114,153,.1);border-color:rgba(251,114,153,.25);transform:translateY(-1px)}

/* 搜索 */
.sbar2{max-width:1200px;margin:0 auto;padding:14px 20px 0}
.sinp{width:100%;max-width:380px;display:block;margin:0 auto;padding:9px 16px;border-radius:24px;border:1px solid var(--border);background:var(--bg1);color:var(--t1);font-size:.9em;outline:none;transition:all .2s}
.sinp:focus{border-color:var(--pink);box-shadow:0 0 0 3px rgba(251,114,153,.15)}
.sinp::placeholder{color:var(--t3)}

/* 内容 */
.container{max-width:1200px;margin:0 auto;padding:30px 20px}
.tag-section{margin-bottom:50px;animation:fiu .5s ease}
@keyframes fiu{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
.tag-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid var(--border)}
.tag-title{font-size:1.4em;font-weight:700;display:flex;align-items:center;gap:10px}
.tag-icon{font-size:1.2em}
.tag-cnt{color:var(--t3);font-size:.82em;background:rgba(255,255,255,.04);padding:3px 10px;border-radius:12px}

/* 视频网格 */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}
.card{background:var(--bg2);border-radius:14px;overflow:hidden;cursor:pointer;transition:all .3s cubic-bezier(.25,.8,.25,1);border:1px solid var(--border);position:relative}
.card:hover{transform:translateY(-6px);box-shadow:0 12px 40px rgba(0,0,0,.5),0 0 0 1px var(--pink);background:var(--bg2h)}
.card-rank{position:absolute;top:10px;left:10px;background:rgba(0,0,0,.75);color:var(--pink);font-weight:800;font-size:.8em;padding:3px 10px;border-radius:6px;z-index:2;backdrop-filter:blur(4px)}
.card-q{position:absolute;top:10px;right:10px;font-size:.72em;padding:3px 9px;border-radius:6px;z-index:2;font-weight:600;backdrop-filter:blur(4px)}
.q-god{background:linear-gradient(135deg,#ffd700,#ffaa00);color:#000;text-shadow:0 0 2px rgba(255,255,255,.5)}
.q-excellent{background:rgba(0,214,126,.92);color:#fff}
.q-good{background:rgba(0,161,214,.85);color:#fff}
.q-normal{background:rgba(139,148,158,.85);color:#fff}
.q-low{background:rgba(110,118,129,.85);color:#fff}

/* 质量分数条 */
.card-score{position:absolute;top:38px;right:10px;width:50px;height:18px;background:rgba(0,0,0,.6);border-radius:9px;overflow:hidden;z-index:2;backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,.1)}
.score-bar{height:100%;transition:width .3s ease}
.score-text{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:.65em;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.8);white-space:nowrap}
.card-hot{position:absolute;top:38px;right:10px;font-size:.68em;padding:2px 8px;border-radius:4px;z-index:2;background:rgba(168,85,247,.85);color:#fff;backdrop-filter:blur(4px)}
.card-cover{aspect-ratio:16/9;background:linear-gradient(135deg,#1a1a2e,#16213e,#1a1a2e);display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}
.cover-img{width:100%;height:100%;object-fit:cover;transition:transform .4s ease}
.card:hover .cover-img{transform:scale(1.05)}
.card-ph{width:100%;height:100%;align-items:center;justify-content:center}
.card-ph svg{width:100%;height:100%}
.dur{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.78);color:#fff;font-size:.72em;padding:2px 8px;border-radius:4px;z-index:2;font-family:monospace;backdrop-filter:blur(4px)}
.card-body{padding:12px 14px 14px}
.card-title{font-size:.9em;font-weight:600;line-height:1.45;margin-bottom:6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;transition:color .2s}
.card:hover .card-title{color:var(--pink)}
.card-meta{display:flex;justify-content:space-between;font-size:.75em;color:var(--t2);margin-bottom:6px}
.card-stats{display:flex;gap:10px;font-size:.7em;color:var(--t3);margin-bottom:6px;flex-wrap:wrap}
.pub{display:inline-block;font-size:.7em;color:var(--green);margin-bottom:8px;opacity:.85}
.card-link{display:inline-flex;align-items:center;gap:4px;color:var(--blue);text-decoration:none;font-size:.8em;font-weight:600;transition:all .2s;padding:3px 0}
.card-link:hover{color:var(--pink);gap:8px}

/* 页脚 */
.footer{text-align:center;padding:30px 20px;color:var(--t3);font-size:.82em;border-top:1px solid var(--border)}
.back-top{position:fixed;bottom:30px;right:30px;width:44px;height:44px;background:linear-gradient(135deg,var(--pink),var(--blue));color:#fff;border:none;border-radius:50%;cursor:pointer;font-size:1.2em;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(251,114,153,.4);opacity:0;transform:translateY(20px);transition:all .3s;z-index:999}
.back-top.visible{opacity:1;transform:translateY(0)}
.back-top:hover{transform:translateY(-3px);box-shadow:0 6px 25px rgba(251,114,153,.6)}

/* 进度提示 */
.progress-info{max-width:1200px;margin:20px auto 0;padding:0 20px}
.progress-info .tag-progress{background:var(--bg1);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:8px;font-size:.82em;color:var(--t2);animation:fiu .3s ease}
.progress-info .tag-progress .tp-tag{color:var(--pink);font-weight:700}

/* ===== 移动端适配 ===== */
@media(max-width:768px){
  .hero{padding:20px 12px 16px}
  .hero h1{font-size:1.4em;margin-bottom:6px}
  .hero .sub{font-size:.88em}
  .hero .stats{gap:8px;margin-top:12px}
  .hero .si{padding:4px 10px;font-size:.78em}

  /* 配置面板 */
  .cfg{padding:10px 12px}
  .cfg-inner{flex-direction:column;align-items:stretch;gap:8px}
  .cfg label{font-size:.78em;margin-bottom:2px}
  .cfg .ti{max-width:100%;min-width:0}
  .cfg .ni{width:100%;text-align:left}
  .cfg .di{width:100%}
  .btn{width:100%;justify-content:center;padding:10px 16px;font-size:.9em}
  .presets{padding:0 12px;margin-top:8px;gap:5px}
  .pt{padding:3px 8px;font-size:.7em}

  /* 状态栏 */
  .sbar{padding:10px 12px;font-size:.78em}

  /* 导航 */
  .nav{padding:8px 12px}
  .nav-item{padding:4px 10px;font-size:.76em}

  /* 搜索 */
  .sbar2{padding:10px 12px 0}
  .sinp{max-width:100%;padding:8px 14px;font-size:.85em}

  /* 内容区 */
  .container{padding:16px 10px}
  .tag-section{margin-bottom:30px}
  .tag-hdr{margin-bottom:14px;padding-bottom:8px}
  .tag-title{font-size:1.15em;gap:6px}

  /* 视频网格 - 单列 */
  .grid{grid-template-columns:1fr;gap:12px}

  /* 卡片移动端优化 */
  .card{border-radius:10px}
  .card:hover{transform:none;box-shadow:0 4px 20px rgba(0,0,0,.4),0 0 0 1px var(--pink)}
  .card-cover{border-radius:10px 10px 0 0}
  .card-body{padding:10px 12px 12px}
  .card-title{font-size:.85em;-webkit-line-clamp:2}
  .card-meta{font-size:.72em}
  .card-stats{gap:8px;font-size:.68em}
  .card-rank{top:8px;left:8px;font-size:.72em;padding:2px 8px}
  .card-q{top:8px;right:8px;font-size:.68em;padding:2px 7px}
  .card-score{top:34px;right:8px;width:42px;height:16px}
  .score-text{font-size:.6em}
  .card-hot{top:54px;right:8px;font-size:.64em}
  .dur{bottom:6px;right:6px;font-size:.68em;padding:2px 6px}
  .pub{font-size:.66em;margin-bottom:6px}
  .card-link{font-size:.76em}

  /* 进度 */
  .progress-info{padding:0 12px}
  .progress-info .tag-progress{padding:10px 12px;font-size:.78em}

  /* 页脚 */
  .footer{padding:20px 12px;font-size:.75em}

  /* 回到顶部 - 移动端缩小 */
  .back-top{bottom:20px;right:16px;width:40px;height:40px;font-size:1em}
}

@media(min-width:769px) and (max-width:1024px){
  .grid{grid-template-columns:repeat(2,1fr)}
  .cfg .ti{min-width:160px}
}

/* 超小屏幕 (<380px) */
@media(max-width:380px){
  .hero h1{font-size:1.2em}
  .hero .stats{flex-direction:column;align-items:center;gap:6px}
  .presets{gap:4px}
  .pt{font-size:.65em;padding:2px 6px}
  .tag-title{font-size:1em}
  .card-body{padding:8px 10px 10px}
  .card-title{font-size:.82em}
}"""


# ========== 完整HTML页面 ==========

def build_full_html(cards_html, nav_html, tags_str, number, days_limit, total_count, tag_count, gen_time):
    """构建完整的HTML页面"""
    now = gen_time or datetime.now()
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>B站优质视频精选</title>
<meta name="referrer" content="no-referrer">
<style>{CSS}</style>
</head>
<body>
<div class="hero">
  <div class="hero-content">
    <h1>📺 B站优质视频精选</h1>
    <p class="sub">自定义标签和数量，实时筛选最近热门优质视频</p>
    <div class="stats">
      <span class="si">📅 <strong>{now.strftime('%Y-%m-%d %H:%M')}</strong></span>
      <span class="si">🎬 共 <strong id="totalCount">{total_count}</strong> 个视频</span>
      <span class="si">🏷️ <strong id="tagCount">{tag_count}</strong> 个标签</span>
    </div>
  </div>
</div>

<div class="cfg">
  <div class="cfg-inner">
    <label>🏷️ 标签:</label>
    <input type="text" class="ti" id="tagsInput" placeholder="输入标签，逗号/空格分隔..." value="{esc(tags_str)}" />
    <label>🔢 数量:</label>
    <input type="number" class="ni" id="numInput" min="1" max="50" value="{number}" />
    <label>⏰ 时间:</label>
    <select class="di" id="daysInput">
      <option value="7"{' selected' if days_limit==7 else ''}>最近7天</option>
      <option value="14"{' selected' if days_limit==14 else ''}>最近14天</option>
      <option value="30"{' selected' if days_limit==30 else ''}>最近30天</option>
      <option value="90"{' selected' if days_limit==90 else ''}>最近90天</option>
      <option value="0"{' selected' if (not days_limit or days_limit>=999) else ''}>不限</option>
    </select>
    <button class="btn" id="btnRefresh" onclick="doRefresh()">
      <span class="sp"></span>
      <span class="bt">🔄 刷新视频</span>
    </button>
  </div>
  <div class="presets" id="presetTags"></div>
</div>

<div class="sbar" id="statusBar"></div>

<nav class="nav" id="navBar">
  <div class="nav-inner" id="navInner">{nav_html}</div>
</nav>

<div class="sbar2">
  <input type="text" class="sinp" placeholder="🔍 搜索页面内的视频标题或UP主..." id="searchInput" />
</div>

<div class="container" id="mainContent">
  {cards_html}
</div>

<div class="footer">
  <p>📺 B站优质视频筛选工具 v3.0 · 在浏览器中直接自定义标签和数量</p>
  <p style="margin-top:6px;font-size:.75em">点击预设标签快速添加 · 修改标签/数量/时间后点击「刷新视频」</p>
</div>

<button class="back-top" id="backTop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>

<script>
const PRESET_TAGS = {json.dumps(list(TAG_ICONS.keys()), ensure_ascii=False)};
const tagsInput = document.getElementById('tagsInput');
const numInput = document.getElementById('numInput');
const daysInput = document.getElementById('daysInput');
const btnRefresh = document.getElementById('btnRefresh');
const mainContent = document.getElementById('mainContent');
const navInner = document.getElementById('navInner');
const statusBar = document.getElementById('statusBar');
const backBtn = document.getElementById('backTop');

// 渲染预设标签
(function(){{
  const box = document.getElementById('presetTags');
  PRESET_TAGS.forEach(tag => {{
    const s = document.createElement('span');
    s.className = 'pt';
    s.textContent = tag;
    s.onclick = () => addTag(tag);
    box.appendChild(s);
  }});
}})();

function addTag(tag) {{
  const cur = tagsInput.value.trim();
  const tags = cur ? cur.split(/[,，\\s]+/).filter(Boolean) : [];
  if (!tags.includes(tag)) {{ tags.push(tag); tagsInput.value = tags.join(', '); }}
  tagsInput.focus();
}}

function showStatus(msg, cls) {{
  statusBar.textContent = msg;
  statusBar.className = 'sbar show ' + cls;
}}

let currentJobId = null;
let pollTimer = null;

async function doRefresh() {{
  const tagsStr = tagsInput.value.trim();
  if (!tagsStr) {{ showStatus('请输入至少一个标签', 'error'); return; }}
  const tags = tagsStr.split(/[,，\\s]+/).filter(Boolean);
  const number = parseInt(numInput.value) || 10;
  const days = parseInt(daysInput.value) || 30;

  btnRefresh.classList.add('loading');
  btnRefresh.disabled = true;

  try {{
    // 1. 提交搜索任务
    const params = new URLSearchParams({{ tags: tags.join(','), number, days }});
    const resp = await fetch('/api/search?' + params.toString());
    const data = await resp.json();

    if (data.error) {{
      showStatus('❌ ' + data.error, 'error');
      btnRefresh.classList.remove('loading');
      btnRefresh.disabled = false;
      return;
    }}

    currentJobId = data.job_id;
    showStatus('🔍 搜索任务已提交，正在后台处理...', 'loading');

    // 2. 轮询任务状态
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollResult(currentJobId), 2000);
    pollResult(currentJobId); // 立即查一次
  }} catch(e) {{
    showStatus('❌ 请求失败: ' + e.message, 'error');
    btnRefresh.classList.remove('loading');
    btnRefresh.disabled = false;
  }}
}}

async function pollResult(jobId) {{
  if (!jobId) return;
  try {{
    const resp = await fetch('/api/result?job_id=' + jobId);
    const data = await resp.json();

    if (data.status === 'processing') {{
      const msg = data.progress || '正在搜索和筛选视频...';
      showStatus('🔍 ' + msg, 'loading');
    }} else if (data.status === 'done') {{
      if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
      mainContent.innerHTML = data.cards_html;
      navInner.innerHTML = data.nav_html;
      document.getElementById('totalCount').textContent = data.total_count;
      document.getElementById('tagCount').textContent = data.tag_count;
      showStatus('✅ 已刷新！共 ' + data.total_count + ' 个视频，覆盖 ' + data.tag_count + ' 个标签', 'ok');
      bindSearch();
      setTimeout(() => {{ statusBar.className = 'sbar'; }}, 5000);
      document.getElementById('navBar').scrollIntoView({{ behavior: 'smooth' }});
      btnRefresh.classList.remove('loading');
      btnRefresh.disabled = false;
      currentJobId = null;
    }} else if (data.status === 'error') {{
      if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
      showStatus('❌ ' + (data.error || '搜索失败'), 'error');
      btnRefresh.classList.remove('loading');
      btnRefresh.disabled = false;
      currentJobId = null;
    }}
  }} catch(e) {{
    // 网络错误，继续轮询
  }}
}}

// 回到顶部
window.addEventListener('scroll', () => {{ backBtn.classList.toggle('visible', window.scrollY > 400); }});

// 页面内搜索
function bindSearch() {{
  const si = document.getElementById('searchInput');
  const cards = document.querySelectorAll('.card');
  const secs = document.querySelectorAll('.tag-section');
  si.oninput = function() {{
    const q = this.value.toLowerCase().trim();
    cards.forEach(c => {{
      const t = (c.querySelector('.card-title')||{{}}).textContent || '';
      const a = (c.querySelector('.card-au')||{{}}).textContent || '';
      c.style.display = (!q || t.toLowerCase().includes(q) || a.toLowerCase().includes(q)) ? '' : 'none';
    }});
    secs.forEach(s => {{
      let vis = false;
      s.querySelectorAll('.card').forEach(c => {{ if(c.style.display!=='none') vis=true; }});
      s.style.display = vis ? '' : 'none';
    }});
  }};
}}
bindSearch();

// 回车刷新
tagsInput.addEventListener('keydown', e => {{ if(e.key==='Enter') doRefresh(); }});
numInput.addEventListener('keydown', e => {{ if(e.key==='Enter') doRefresh(); }});
</script>
</body>
</html>"""


# ========== Web服务器 ==========

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ========== 异步任务管理 ==========

# 全局任务存储（Web服务器使用）
_jobs = {}  # job_id -> {status, progress, result, error}


def _run_search_job(job_id, tags, number, days_limit, min_play):
    """在后台线程中执行搜索任务（带质量评分）"""
    _jobs[job_id]["status"] = "processing"
    _jobs[job_id]["progress"] = f"正在搜索 {len(tags)} 个标签..."

    try:
        videos_by_tag = {}
        for i, tag in enumerate(tags):
            _jobs[job_id]["progress"] = f"搜索 {i+1}/{len(tags)}: #{tag}"
            print(f"  [Job {job_id[:8]}] 搜索 {i+1}/{len(tags)}: #{tag}")

            # 搜索
            raw = search_videos(tag, max_results=80)
            if not raw:
                continue

            filtered = filter_and_rank(raw, min_play=min_play, sort_by_quality=False)
            if not filtered:
                filtered = filter_and_rank(raw, min_play=0, sort_by_quality=False)
                if not filtered:
                    continue

            # 获取详细信息
            target_count = min(number * 3, len(filtered))
            candidates = filtered[:target_count]
            _jobs[job_id]["progress"] = f"获取 #{tag} 的视频详情 ({len(candidates)}个)..."

            enriched = enrich_videos(candidates, days_limit=days_limit, silent=True)

            if not enriched and days_limit:
                enriched = enrich_videos(filtered[:number], days_limit=None, silent=True)

            if not enriched:
                continue

            # enrich后重新进行严格过滤（此时有完整数据）
            # 策略：先尝试严格过滤，如果不够N个，逐步放宽直到凑够
            filtered_strict = filter_and_rank(enriched, min_play=0, sort_by_quality=True, strict_mode=True, enriched=True)
            
            if len(filtered_strict) >= number:
                videos_by_tag[tag] = filtered_strict[:number]
            else:
                # 严格过滤后不够，尝试宽松过滤
                filtered_loose = filter_and_rank(enriched, min_play=0, sort_by_quality=True, strict_mode=False, enriched=True)
                if len(filtered_loose) >= number:
                    videos_by_tag[tag] = filtered_loose[:number]
                else:
                    # 宽松过滤还不够，直接按质量分排序取前N个
                    enriched.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
                    videos_by_tag[tag] = enriched[:number]
            
            if not videos_by_tag[tag]:
                continue
            avg_score = sum(v.get("quality_score", 0) for v in enriched[:number]) / len(enriched[:number]) if enriched[:number] else 0
            print(f"  [Job {job_id[:8]}] #{tag} 平均质量分: {avg_score:.1f}")

        if not videos_by_tag:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "未找到符合条件的视频，请尝试更换标签或扩大时间范围"
            return

        cards_html, nav_html, total_count, tag_count = build_sections(videos_by_tag)

        # 保存配置
        save_config({
            "tags": tags,
            "number": number,
            "days_limit": days_limit if days_limit else 999,
            "min_play": min_play,
        })

        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = {
            "cards_html": cards_html,
            "nav_html": nav_html,
            "total_count": total_count,
            "tag_count": tag_count,
        }
        print(f"  [Job {job_id[:8]}] 完成! {total_count}个视频, {tag_count}个标签")

    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = f"搜索出错: {str(e)}"
        print(f"  [Job {job_id[:8]}] 错误: {e}")


class RequestHandler(BaseHTTPRequestHandler):
    """处理Web请求"""

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            self._serve_index()
        elif parsed.path == "/api/search":
            self._serve_search(parse_qs(parsed.query))
        elif parsed.path == "/api/result":
            self._serve_result(parse_qs(parsed.query))
        elif parsed.path == "/api/status":
            self._serve_status()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_index(self):
        """主页"""
        config = load_config()
        tags = config.get("tags", DEFAULT_TAGS)
        number = config.get("number", 10)
        days = config.get("days_limit", 30)
        if days >= 999:
            days = 0

        tags_str = ", ".join(tags)
        html = build_full_html(
            cards_html='<div style="text-align:center;padding:60px;color:var(--t3)"><p style="font-size:1.2em;margin-bottom:16px">👆 配置标签和数量后点击「刷新视频」</p><p>或直接点击上方预设标签快速添加</p></div>',
            nav_html="", tags_str=tags_str, number=number,
            days_limit=days, total_count=0, tag_count=0,
            gen_time=datetime.now()
        )
        self._send_html(html)

    def _serve_search(self, params):
        """提交搜索任务（异步）"""
        config = load_config()
        default_tags = config.get("tags", DEFAULT_TAGS)

        tags_raw = params.get("tags", [", ".join(default_tags)])[0]
        tags = [t.strip() for t in re.split(r'[,，\s]+', tags_raw) if t.strip()]
        number = int(params.get("number", ["10"])[0])
        days = int(params.get("days", ["30"])[0])
        days_limit = days if days > 0 else None
        min_play = 500

        if not tags:
            self._send_json({"error": "请输入至少一个标签"})
            return

        # 创建异步任务
        import uuid
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {
            "status": "processing",
            "progress": "正在启动搜索任务...",
            "result": None,
            "error": None,
        }

        # 在后台线程中执行搜索
        thread = threading.Thread(
            target=_run_search_job,
            args=(job_id, tags, number, days_limit, min_play),
            daemon=True
        )
        thread.start()

        self._send_json({"job_id": job_id, "status": "processing"})

    def _serve_result(self, params):
        """查询搜索任务结果"""
        job_id = params.get("job_id", [None])[0]
        if not job_id or job_id not in _jobs:
            self._send_json({"status": "error", "error": "无效的任务ID"})
            return

        job = _jobs[job_id]
        if job["status"] == "done":
            resp = {"status": "done"}
            resp.update(job["result"])
            self._send_json(resp)
        elif job["status"] == "error":
            self._send_json({"status": "error", "error": job["error"]})
        else:
            self._send_json({"status": "processing", "progress": job["progress"]})

    def _serve_status(self):
        """健康检查"""
        active = sum(1 for j in _jobs.values() if j["status"] == "processing")
        self._send_json({"status": "ok", "time": datetime.now().isoformat(), "active_jobs": active})

    def _send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [{ts}] {args[0]}")


# ========== 静态HTML生成 ==========

def generate_static_html(videos_by_tag, output_path, title="B站优质视频精选",
                         tags=None, number=10, days_limit=30, gen_time=None):
    """生成静态HTML文件"""
    cards_html, nav_html, total_count, tag_count = build_sections(videos_by_tag)
    tags_str = ", ".join(tags) if tags else ""
    html = build_full_html(cards_html, nav_html, tags_str, number,
                            days_limit or 999, total_count, tag_count, gen_time)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ HTML已生成: {output_path}")
    print(f"   共 {total_count} 个视频，{tag_count} 个标签")


# ========== 交互式CLI ==========

def interactive_setup():
    """交互式命令行配置"""
    print("\n" + "=" * 60)
    print("📺 B站优质视频筛选工具 - 交互式配置")
    print("=" * 60)

    config = load_config()
    last_tags = config.get("tags", DEFAULT_TAGS)
    last_number = config.get("number", 10)
    last_days = config.get("days_limit", 30)

    # 标签选择
    print("\n📌 预设标签分组:")
    for i, (name, tags) in enumerate(TAG_PRESETS.items(), 1):
        print(f"  [{i}] {name}: {', '.join(tags)}")
    print(f"  [0] 自定义输入")

    choice = input(f"\n  选择分组 (默认回车=上次配置): ").strip()
    if choice == "0":
        tags_input = input("  输入标签(逗号/空格分隔): ").strip()
        tags = [t.strip() for t in re.split(r'[,，\s]+', tags_input) if t.strip()]
    elif choice and choice.isdigit():
        idx = int(choice) - 1
        preset_names = list(TAG_PRESETS.keys())
        if 0 <= idx < len(preset_names):
            tags = TAG_PRESETS[preset_names[idx]]
        else:
            tags = last_tags
    else:
        tags = last_tags

    if not tags:
        tags = DEFAULT_TAGS
    print(f"  当前标签: {', '.join(tags)}")

    # 数量
    n = input(f"  每标签数量 (默认{last_number}): ").strip()
    number = int(n) if n.isdigit() else last_number
    number = max(1, min(number, 50))

    # 时间
    d = input(f"  时间范围天数 7/14/30/90/0不限 (默认{last_days}): ").strip()
    days_limit = int(d) if d.isdigit() else last_days
    if days_limit == 0:
        days_limit = None

    # 保存
    save_cfg = input("  保存配置供下次使用? (Y/n): ").strip().lower()
    if save_cfg != 'n':
        save_config({"tags": tags, "number": number, "days_limit": days_limit or 999, "min_play": 500})

    output = input(f"  输出文件 (默认 bilibili-picks.html): ").strip() or "bilibili-picks.html"
    return tags, number, days_limit, 500, output


# ========== 主入口 ==========

def main():
    parser = argparse.ArgumentParser(description="B站优质视频筛选工具 v3.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python bilibili_picker.py --web          # Web模式（推荐！）
  python bilibili_picker.py --web --port 9000  # 指定端口
  python bilibili_picker.py -i             # 交互式命令行
  python bilibili_picker.py --auto         # 使用上次配置生成静态HTML
        """)
    parser.add_argument("--web", action="store_true", help="启动Web服务器（推荐）")
    parser.add_argument("--port", type=int, default=8899, help="Web端口 (默认8899)")
    parser.add_argument("--tags", nargs="+", help="搜索标签")
    parser.add_argument("-n", "--number", type=int, default=10, help="每标签视频数")
    parser.add_argument("--days", type=int, default=30, help="时间范围天数")
    parser.add_argument("--min-play", type=int, default=500, help="最低播放量")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互式CLI")
    parser.add_argument("--auto", action="store_true", help="使用上次配置")
    parser.add_argument("-o", "--output", help="输出HTML路径")
    parser.add_argument("--title", default="B站优质视频精选")


    args = parser.parse_args()

    # Web模式
    if args.web:
        start_web_server(args.port)
        return

    # 静态HTML生成模式
    if args.interactive:
        tags, number, days_limit, min_play, output = interactive_setup()
    elif args.auto:
        config = load_config()
        if config:
            tags = config.get("tags", DEFAULT_TAGS)
            number = config.get("number", 10)
            days_limit = config.get("days_limit", 30)
            if days_limit >= 999:
                days_limit = None
            min_play = config.get("min_play", 500)
            output = config.get("output", "bilibili-picks.html")
            print("✅ 使用已保存的配置")
        else:
            print("⚠ 无已保存配置，使用默认值")
            tags, number, days_limit, min_play, output = DEFAULT_TAGS, 10, 30, 500, "bilibili-picks.html"
    elif args.tags:
        tags = args.tags
        number = args.number
        days_limit = args.days if args.days > 0 else None
        min_play = args.min_play
        output = args.output or "bilibili-picks.html"
    else:
        print("💡 未指定参数，进入交互模式")
        print("   推荐: python bilibili_picker.py --web\n")
        tags, number, days_limit, min_play, output = interactive_setup()

    output = args.output or output
    gen_time = datetime.now()

    print(f"\n{'='*60}")
    print(f"📺 B站优质视频筛选工具 v3.0")
    print(f"{'='*60}")
    print(f"  标签: {', '.join(tags)}")
    print(f"  数量: 每标签 {number}")
    print(f"  时间: {'最近 '+str(days_limit)+' 天' if days_limit else '不限'}")
    print(f"  最低播放: {min_play}")
    print(f"{'='*60}")

    videos_by_tag = do_search(tags, number, days_limit, min_play=min_play)

    if not videos_by_tag:
        print("\n❌ 未找到符合条件的视频")
        sys.exit(1)

    generate_static_html(videos_by_tag, output, args.title, tags, number, days_limit, gen_time)
    print("\n🎉 完成！")
    print(f"💡 推荐: python bilibili_picker.py --web 启动浏览器模式")


def start_web_server(port=8899):
    """启动Web服务器"""
    import webbrowser


    config = load_config()
    default_tags = config.get("tags", DEFAULT_TAGS)
    default_number = config.get("number", 10)
    default_days = config.get("days_limit", 30)
    if default_days >= 999:
        default_days = 0

    # 云端部署：从环境变量 PORT 读取（Railway/Render 等平台会注入）
    env_port = os.environ.get("PORT")
    if env_port:
        port = int(env_port)

    # 云端环境绑定 0.0.0.0，本地绑定 127.0.0.1
    bind_host = "0.0.0.0" if env_port else "127.0.0.1"

    # 尝试绑定端口
    for attempt in range(5):
        try:
            server = ThreadedHTTPServer((bind_host, port), RequestHandler)
            break
        except OSError:
            port += 1
    else:
        print(f"❌ 无法绑定端口，请检查")
        sys.exit(1)

    # 本地模式才自动打开浏览器
    if not env_port:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    print(f"\n{'='*60}")
    print(f"🌐 B站视频精选 Web 服务已启动")
    print(f"{'='*60}")
    print(f"  📂 http://{bind_host}:{port}")
    print(f"  🏷️  默认标签: {', '.join(default_tags)}")
    print(f"  🔢 默认数量: 每标签 {default_number}")
    print(f"  ⏰ 默认时间: {'最近'+str(default_days)+'天' if default_days else '不限'}")
    print(f"\n  💡 在浏览器中直接修改标签/数量，点击「刷新视频」")
    print(f"  按 Ctrl+C 停止")
    print(f"{'='*60}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
