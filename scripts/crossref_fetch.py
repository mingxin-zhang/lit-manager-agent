"""
CrossRef 文章搜索脚本

用法:
  # 批量模式：拉取期刊指定年份全部文章
  python crossref_fetch.py --from 2021 --to 2025 --tier top5 -o search_fetched.csv

  # 搜索模式：关键词搜索 + 期刊过滤（结果自带 CrossRef 关联度 score）
  python crossref_fetch.py --query "minimum wage employment" --tier top5 --from 2021 --to 2025 -o search_fetched.csv
"""
import argparse
import csv
import os
import sys
import time

import requests
import yaml

CROSSREF_API = "https://api.crossref.org/works"
POLITE_EMAIL = ""
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "config", "journals.yaml")


def load_journals(tier: str = "all") -> dict:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    if tier == "top5":
        return config.get("top5", {})
    elif tier == "field_top":
        return config.get("field_top", {})
    return {**config.get("top5", {}), **config.get("field_top", {})}


def build_issn_filter(tier: str) -> str:
    journals = load_journals(tier)
    return ",".join(f"issn:{issn}" for issn in journals.values())


def parse_item(item: dict, short_name: str = "") -> dict:
    container = item.get("container-title", [])
    title = (item.get("title") or [""])[0]
    authors = "; ".join(
        f"{a.get('family', '')}, {a.get('given', '')}"
        for a in item.get("author", [])
    )
    year = ""
    issued = item.get("issued", {}).get("date-parts", [[None]])
    if issued and issued[0]:
        year = str(issued[0][0]) if issued[0][0] else ""

    # 解析 ISSN
    issn_list = item.get("issn-type", []) or []
    issn_val = ""
    for i in issn_list:
        if isinstance(i, dict) and i.get("type") == "print":
            issn_val = i.get("value", "")
            break
    if not issn_val and issn_list:
        issn_val = issn_list[0].get("value", "") if isinstance(issn_list[0], dict) else ""

    return {
        "journal_short": short_name,
        "issn": issn_val,
        "year": year,
        "journal": container[0] if container else "",
        "title": title,
        "authors": authors,
        "doi": item.get("DOI", ""),
        "url": item.get("URL", ""),
        "abstract": item.get("abstract", ""),
        "type": item.get("type", ""),
        "issue": item.get("issue", ""),
        "volume": item.get("volume", ""),
        "score": item.get("score", ""),
        "is_referenced_by_count": item.get("is-referenced-by-count", ""),
    }


# ---- 搜索模式 ----
def search_works(query: str, tier: str, from_year: int, to_year: int,
                 max_rows: int = 100, custom_issn: str = None) -> list[dict]:
    if custom_issn:
        issn_filter = f"issn:{custom_issn}"
    else:
        issn_filter = build_issn_filter(tier)
    filters = [
        f"from-pub-date:{from_year}-01-01",
        f"until-pub-date:{to_year}-12-31",
        f"type:journal-article",
    ]

    params = {
        "query": query,
        "filter": f"{issn_filter},{','.join(filters)}",
        "rows": max_rows,
        "mailto": POLITE_EMAIL,
    }

    results = []
    url = CROSSREF_API

    while url:
        resp = requests.get(url, params=params if url == CROSSREF_API else None,
                            headers={"User-Agent": f"mailto:{POLITE_EMAIL}"})
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        items = message.get("items", [])
        for item in items:
            results.append(parse_item(item))

        # cursor-based pagination
        next_cursor = message.get("next-cursor")
        if next_cursor and len(results) < max_rows:
            url = f"{CROSSREF_API}?cursor={next_cursor}"
            print(f"  Fetching next page (cursor: {next_cursor})...")
            time.sleep(0.3)
        else:
            url = None

    return results


# ---- 批量模式 ----
def fetch_by_issn(issn, short_name, from_year, to_year, rows=1000, sleep_sec=0.5):
    results = []
    for year in range(from_year, to_year + 1):
        params = {
            "filter": f"issn:{issn},from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,type:journal-article",
            "rows": rows,
        }
        try:
            resp = requests.get(CROSSREF_API, params=params,
                                headers={"User-Agent": f"mailto:{POLITE_EMAIL}"})
            resp.raise_for_status()
            items = resp.json().get("message", {}).get("items", [])
            for item in items:
                results.append(parse_item(item, short_name))
            print(f"  {short_name} {year}: {len(items)} articles")
        except Exception as e:
            print(f"  [ERROR] {short_name} {year}: {e}", file=sys.stderr)
        time.sleep(sleep_sec)
    return results


def save_csv(results: list[dict], output_path: str):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fieldnames = [
        "journal_short", "issn", "year", "journal", "title",
        "authors", "doi", "url", "abstract", "type", "issue", "volume",
        "score", "is_referenced_by_count",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    print(f"Saved {len(results)} articles to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="CrossRef article fetcher (search + bulk)")
    parser.add_argument("--query", default=None,
                        help="Search query (search mode)")
    parser.add_argument("--from", dest="from_year", type=int, default=2021)
    parser.add_argument("--to", dest="to_year", type=int, default=2025)
    parser.add_argument("--tier", choices=["top5", "field_top", "all"], default="top5")
    parser.add_argument("--issn", default=None,
                        help="Custom ISSN (overrides --tier when used with --query)")
    parser.add_argument("-o", "--output", required=True, help="Output CSV path")
    args = parser.parse_args()

    if args.query:
        print(f'Search mode: "{args.query}" ({args.tier} journals, {args.from_year}-{args.to_year})')
        if args.issn:
            print(f'  Custom ISSN: {args.issn} (overrides tier)')
        results = search_works(args.query, args.tier, args.from_year, args.to_year,
                               custom_issn=args.issn)
    else:
        journals = load_journals(args.tier)
        print(f"Bulk mode: {len(journals)} journals ({args.tier}), {args.from_year}-{args.to_year}")
        results = []
        for short_name, issn in journals.items():
            print(f"\n==== {short_name} (ISSN: {issn}) ====")
            results.extend(fetch_by_issn(issn, short_name, args.from_year, args.to_year))

    print(f"\nTotal: {len(results)} articles")
    save_csv(results, args.output)


if __name__ == "__main__":
    main()
