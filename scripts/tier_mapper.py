"""tier_mapper.py — 机器判定 provenance tier，LLM 不参与。

规则（见 perovskite-scout-playbook.md §2.5 / spec §5）：
  T1 进核心: arxiv.org, doi.org, 期刊官网, 公司官网, nrel.gov
  T2 进核心: nomad-lab.eu, perovskitedatabase.com, 机构数据库
  T3 单列:   媒体域名 (pv-magazine.com, pv-tech.org, 中文行业媒体)
  T4 不展示: 社媒, 未知 / 无一手来源

边界规则（用户 2026-07-10 修正）：
  新闻稿 / 披露平台 (prnewswire / businesswire / globenewswire / 交易所 / 巨潮 / 公众号)
  非公司官网域名，且 MVP 阶段无法识别发布主体，**默认映射 T3**，绝不粗暴升 T1。
  仅当后续能识别发布主体为目标公司 / 交易所披露主体时才升 T1（阶段2 再做）。

tier 由来源域名硬判定，LLM 只能解释 tier，不能决定 tier（红线 R3）。
"""

from urllib.parse import urlparse

# 精确域名 -> tier
_EXACT = {
    "arxiv.org": "T1",
    "doi.org": "T1",
    "dx.doi.org": "T1",
    "nrel.gov": "T1",
    "www.nrel.gov": "T1",
    "nomad-lab.eu": "T2",
    "nomad.readthedocs.io": "T2",
    "perovskitedatabase.com": "T2",
    "www.perovskitedatabase.com": "T2",
}

# 后缀(域名结尾) -> tier，用于机构库 / 期刊官网等大类
_SUFFIX = [
    (".nrel.gov", "T1"),
    (".nomad-lab.eu", "T2"),
]

# 新闻稿 / 披露平台 -> 默认 T3（边界规则，不升 T1）
_PRESS = {
    "prnewswire.com",
    "www.prnewswire.com",
    "businesswire.com",
    "www.businesswire.com",
    "globenewswire.com",
    "www.globenewswire.com",
    "newswire.com",
    "cninfo.com.cn",          # 巨潮（交易所披露）
    "www.cninfo.com.cn",
    "mp.weixin.qq.com",       # 公众号文章
}

# 媒体域名 -> T3
_MEDIA = {
    "pv-magazine.com",
    "www.pv-magazine.com",
    "pv-tech.org",
    "www.pv-tech.org",
    "greentechmedia.com",
    "solarmagazine.com",
    "perovskite-info.com",
    "www.perovskite-info.com",
}

# 社媒 / 未知 -> T4
_SOCIAL = {
    "twitter.com",
    "x.com",
    "weibo.com",
    "www.weibo.com",
    "reddit.com",
    "www.reddit.com",
}


def _normalize(domain: str) -> str:
    d = domain.lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def tier_for_url(url: str) -> str:
    """返回 url 的 provenance tier (T1-T4)，纯规则，无 LLM。"""
    if not url:
        return "T4"
    host = urlparse(url).netloc.lower()
    d = _normalize(host)
    if not d:
        return "T4"
    # 1) 精确匹配
    if d in _EXACT:
        return _EXACT[d]
    # 2) 新闻稿 / 披露平台 -> T3（边界规则）
    if d in _PRESS:
        return "T3"
    # 3) 媒体 -> T3
    if d in _MEDIA:
        return "T3"
    # 4) 社媒 -> T4
    if d in _SOCIAL:
        return "T4"
    # 5) 后缀匹配（机构库 / 期刊官网）
    for suffix, tier in _SUFFIX:
        if d.endswith(suffix):
            return tier
    # 6) 未知 -> T4
    return "T4"


if __name__ == "__main__":
    import sys

    for u in sys.argv[1:]:
        print(f"{u} -> {tier_for_url(u)}")
