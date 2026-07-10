#!/usr/bin/env python3
"""feed_dedup.py — 跨 feed 去重 (论文 vs 行业).

为什么需要:
  Oxford PV 这类主体既发 arXiv 论文, 又在官网/newsroom 发公告, 会同时
  进 feed-papers.json 和 feed-industry.json. 微信推送里同一条出现两次就很蠢.

去重规则 (用户拍板: 第一版只做精确规则, 不做相似度):
  1. normalized_title 完全相同
  2. url 完全相同
  3. doi 完全相同

实现只依赖 item 上的 title / url / doi 三个字段, 不引入任何外部依赖.
行业条目目前没有 doi(置 None), 所以 DOI 规则对它们自然不触发, 是对称的.
"""

import re

# 归一标题: 转小写, 去所有非 [字母/数字/汉字] 的字符, 折叠空白
_TITLE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def normalize_title(t: str) -> str:
    t = (t or "").lower()
    t = _TITLE_RE.sub(" ", t)
    return t.strip()


def build_signatures(paper_items: list[dict]) -> tuple[set, set, set]:
    """从论文 feed 构建三套签名集合: (标题, URL, DOI)."""
    titles = set()
    urls = set()
    dois = set()
    for it in paper_items:
        if it.get("title"):
            titles.add(normalize_title(it["title"]))
        if it.get("url"):
            urls.add(it["url"])
        doi = it.get("doi")
        if doi:  # 只收非空 DOI; 行业条目 doi=None 不会污染
            dois.add(doi)
    return titles, urls, dois


def is_dup_of_papers(item: dict,
                     sig_titles: set,
                     sig_urls: set,
                     sig_dois: set) -> str | None:
    """返回命中规则名 ('normalized_title' / 'url' / 'doi') 或 None."""
    if normalize_title(item.get("title")) in sig_titles:
        return "normalized_title"
    if item.get("url") and item.get("url") in sig_urls:
        return "url"
    doi = item.get("doi")
    if doi and doi in sig_dois:
        return "doi"
    return None


def dedup_industry(industry_items: list[dict],
                   paper_items: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """从 industry_items 中剔除与论文重复的条目.

    返回 (保留列表, [(被删条目, 命中规则), ...]).
    """
    st, su, sd = build_signatures(paper_items)
    kept: list[dict] = []
    removed: list[tuple[dict, str]] = []
    for it in industry_items:
        reason = is_dup_of_papers(it, st, su, sd)
        if reason:
            removed.append((it, reason))
        else:
            kept.append(it)
    return kept, removed


if __name__ == "__main__":
    # 快速自测
    papers = [
        {"title": "High-efficiency perovskite solar cell", "url": "https://arxiv.org/abs/1234",
         "doi": "10.48550/arXiv.1234"},
        {"title": "Oxford PV reaches 28%", "url": "https://oxfordpv.com/news/28", "doi": None},
    ]
    ind = [
        {"title": "Oxford PV reaches 28%", "url": "https://oxfordpv.com/news/28", "doi": None},   # 标题+URL 命中
        {"title": "Perovskite-Info weekly roundup", "url": "https://perovskite-info.com/x", "doi": None},  # 不重复
    ]
    k, rm = dedup_industry(ind, papers)
    assert len(k) == 1 and k[0]["title"].startswith("Perovskite-Info")
    assert len(rm) == 1 and rm[0][1] in ("normalized_title", "url")
    print("feed_dedup self-test OK")
