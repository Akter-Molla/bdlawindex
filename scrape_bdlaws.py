#!/usr/bin/env python3
"""
bdlaws.minlaw.gov.bd স্ক্র্যাপার
=================================
এই স্ক্রিপ্ট বাংলাদেশের সকল আইনের তালিকা ও পূর্ণ টেক্সট সংগ্রহ করে JSON ফাইলে সেভ করে।

গুরুত্বপূর্ণ:
- এই স্ক্রিপ্ট Claude-এর sandbox-এ চলবে না (নেটওয়ার্ক অ্যাক্সেস নেই),
  আপনার নিজের কম্পিউটারে বা GitHub Actions runner-এ চালাতে হবে।
- সরকারি সার্ভারে অতিরিক্ত চাপ এড়াতে REQUEST_DELAY মেনে চলুন (কমাবেন না)।
- আইনের মূল টেক্সট পাবলিক ডোমেইন প্রকৃতির, তবে সোর্স হিসেবে
  bdlaws.minlaw.gov.bd-কে ক্রেডিট দেওয়া ভালো অভ্যাস।

ব্যবহার:
    pip install requests beautifulsoup4 --break-system-packages
    python3 scrape_bdlaws.py --stage volumes      # ধাপ ১: ভলিউম থেকে আইনের লিস্ট বানান
    python3 scrape_bdlaws.py --stage acts          # ধাপ ২: প্রতিটা আইনের ফুল টেক্সট নামান
    python3 scrape_bdlaws.py --stage acts --resume # মাঝপথে বন্ধ হলে আবার শুরু করতে
"""

import argparse
import json
import re
import time
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "http://bdlaws.minlaw.gov.bd"
OUT_DIR = Path("bdlaws_data")
OUT_DIR.mkdir(exist_ok=True)

ACTS_INDEX_FILE = OUT_DIR / "acts_index.json"       # ধাপ ১-এর আউটপুট (সব আইনের লিস্ট)
ACTS_FULL_DIR = OUT_DIR / "acts"                     # ধাপ ২-এর আউটপুট (প্রতিটা আইন = একটা ফাইল)
ACTS_FULL_DIR.mkdir(exist_ok=True)

REQUEST_DELAY = 1.5          # প্রতি রিকোয়েস্টের মাঝে বিরতি (সেকেন্ড) — সরকারি সার্ভার সম্মান করুন
MAX_VOLUMES = 58             # ভলিউম-১ থেকে ভলিউম-৫৮ পর্যন্ত (সাইট চেক করে বসানো)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LegalArchiveBot/1.0; +personal research project)"
}


def fetch(url: str) -> BeautifulSoup:
    """UTF-16 এনকোডিং সঠিকভাবে হ্যান্ডেল করে পেজ আনে।"""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    # সাইটটি UTF-16 এ সার্ভ করে — apparent_encoding ভুল হলে ম্যানুয়ালি ঠিক করুন
    if resp.encoding is None or "utf" not in (resp.encoding or "").lower():
        resp.encoding = resp.apparent_encoding
    return BeautifulSoup(resp.text, "html.parser")


def stage_volumes():
    """সব ভলিউম পেজ ঘুরে প্রতিটা আইনের নাম + লিংক সংগ্রহ করে acts_index.json বানায়।"""
    all_acts = []
    seen_ids = set()

    for vol in range(1, MAX_VOLUMES + 1):
        url = f"{BASE}/volume-{vol}.html"
        print(f"[ভলিউম {vol}/{MAX_VOLUMES}] fetching {url}")
        try:
            soup = fetch(url)
        except Exception as e:
            print(f"  ⚠️ ব্যর্থ: {e}")
            continue

        # আইনের লিংক সাধারণত act-<id>.html প্যাটার্নে থাকে
        for a in soup.select("a[href*='act-']"):
            href = a.get("href", "")
            m = re.search(r"act-(\d+)\.html", href)
            if not m:
                continue
            act_id = m.group(1)
            if act_id in seen_ids:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            seen_ids.add(act_id)
            all_acts.append({
                "act_id": act_id,
                "title": title,
                "url": f"{BASE}/act-{act_id}.html",
                "volume": vol,
            })

        time.sleep(REQUEST_DELAY)

    ACTS_INDEX_FILE.write_text(
        json.dumps(all_acts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✅ সর্বমোট {len(all_acts)}টি আইন পাওয়া গেছে → {ACTS_INDEX_FILE}")


def parse_act_toc_page(soup: BeautifulSoup) -> dict:
    """act-XXXX.html (TOC পেজ) থেকে শিরোনাম, আইন নম্বর, তারিখ, প্রস্তাবনা ও
    ধারার লিস্ট (নাম + লিংক) বের করে। আসল HTML স্যাম্পল দেখে এই সিলেক্টরগুলো
    কনফার্ম করা হয়েছে।"""
    title_el = soup.select_one(".bg-act-section h3")
    title = title_el.get_text(strip=True) if title_el else ""

    act_no_el = soup.select_one(".bg-act-section h4")
    act_no = act_no_el.get_text(" ", strip=True) if act_no_el else ""

    date_el = soup.select_one(".publish-date")
    publish_date = date_el.get_text(strip=True) if date_el else ""

    preamble_el = soup.select_one(".pad-right")
    preamble = preamble_el.get_text(" ", strip=True) if preamble_el else ""

    # "পূর্ণ আইন দেখুন" বাটনের লিংক → act-details-XXXX.html (পূর্ণ টেক্সট এখানে)
    full_link_el = soup.select_one("a.view-full-law-button")
    full_url = None
    if full_link_el and full_link_el.get("href"):
        full_url = BASE + full_link_el["href"] if full_link_el["href"].startswith("/") else full_link_el["href"]

    sections_toc = []
    for p in soup.select("p.act-section-name a"):
        href = p.get("href", "")
        sections_toc.append({
            "label": p.get_text(" ", strip=True),
            "url": BASE + href if href.startswith("/") else href,
        })

    return {
        "title": title,
        "act_no": act_no,
        "publish_date": publish_date,
        "preamble": preamble,
        "full_text_url": full_url,
        "sections_toc": sections_toc,
    }


def parse_act_details_page(soup: BeautifulSoup) -> dict:
    """act-details-XXXX.html (পূর্ণ আইন পেজ) থেকে প্রতিটা ধারা আলাদাভাবে বের করে।
    আসল HTML স্যাম্পল (ওয়াক্‌ফ আইন, ২০১৩) দেখে কনফার্ম করা স্ট্রাকচার:
        <div class="row lineremoves">
            <div class="txt-head">ধারার শিরোনাম</div>
            <div class="txt-details" id="sec-dec">১। মূল টেক্সট...</div>
        </div>
    """
    sections = []
    for row in soup.select("div.row.lineremoves"):
        head_el = row.select_one(".txt-head")
        body_el = row.select_one(".txt-details")
        if not body_el:
            continue

        # <div class="clbr"> ও <div class="na"> গুলো প্যারাগ্রাফ/লাইন বিভাজক হিসেবে কাজ করে —
        # টেক্সট বের করার আগে এগুলোকে নিউলাইনে রূপান্তর করি, যাতে উপ-ধারার গঠন বজায় থাকে
        for br_div in body_el.select("div.clbr, div.na"):
            br_div.replace_with("\n")

        section_title = head_el.get_text(" ", strip=True) if head_el else ""
        section_text = body_el.get_text(" ", strip=True)
        # ঐ HTML-এ একাধিক স্পেস/নিউলাইন থাকতে পারে, সেগুলো পরিষ্কার করি
        section_text = re.sub(r"[ \t]+", " ", section_text)
        section_text = re.sub(r"\n\s*\n+", "\n", section_text).strip()

        # ধারার শুরুতে বাংলা/ইংরেজি সংখ্যা + । দিয়ে ধারা নম্বর বের করা
        m = re.match(r"^([০-৯0-9]+)\s*[।.]", section_text)
        section_no = m.group(1) if m else None

        sections.append({
            "section_no": section_no,
            "title": section_title,
            "text": section_text,
        })

    return {"sections": sections}


def stage_acts(resume: bool = False, fetch_full_text: bool = True):
    if not ACTS_INDEX_FILE.exists():
        print("❌ আগে --stage volumes চালান, acts_index.json লাগবে")
        sys.exit(1)

    acts = json.loads(ACTS_INDEX_FILE.read_text(encoding="utf-8"))
    print(f"মোট {len(acts)}টি আইন ডাউনলোড করা হবে...")

    for i, act in enumerate(acts, 1):
        out_path = ACTS_FULL_DIR / f"act-{act['act_id']}.json"
        if resume and out_path.exists():
            continue

        print(f"[{i}/{len(acts)}] {act['title'][:50]}...")
        try:
            soup = fetch(act["url"])
            toc_data = parse_act_toc_page(soup)
            record = {**act, **toc_data}

            # ধাপ ২: পূর্ণ টেক্সট পেজ (এক আইন = এক অতিরিক্ত রিকোয়েস্ট)
            if fetch_full_text and toc_data.get("full_text_url"):
                time.sleep(REQUEST_DELAY)
                try:
                    full_soup = fetch(toc_data["full_text_url"])
                    record.update(parse_act_details_page(full_soup))
                except Exception as e:
                    print(f"    ⚠️ পূর্ণ টেক্সট আনতে ব্যর্থ: {e}")

            out_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"  ⚠️ ব্যর্থ: {e}")

        time.sleep(REQUEST_DELAY)

    print(f"\n✅ সম্পন্ন। ফাইলগুলো আছে: {ACTS_FULL_DIR}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["volumes", "acts"], required=True)
    parser.add_argument("--resume", action="store_true", help="আগে যা হয়ে গেছে বাদ দিয়ে চালাবে")
    parser.add_argument("--toc-only", action="store_true", help="শুধু ধারার লিস্ট আনবে, পূর্ণ টেক্সট বাদ")
    args = parser.parse_args()

    if args.stage == "volumes":
        stage_volumes()
    else:
        stage_acts(resume=args.resume, fetch_full_text=not args.toc_only)
