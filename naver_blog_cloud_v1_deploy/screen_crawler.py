# -*- coding: utf-8 -*-
"""네이버 블로그 검색화면 수집기 V2.2
네이버 검색 API를 전혀 사용하지 않습니다. 실제 브라우저 DOM에 나타난 게시글 링크만 읽습니다.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
APP_TITLE = "네이버 블로그 검색화면 수집기 V2.2 (API 불필요)"
JS_FILE = SCRIPT_DIR / "extract_visible_results.js"
BLOG_NAME_JS_FILE = SCRIPT_DIR / "extract_visible_blogname.js"
COLUMNS = [
    "검색 키워드", "화면 수집 순번", "게시글 제목", "블로그명", "게시일(화면 표기)",
    "검색화면 요약", "블로그 ID", "게시글 주소", "화면 링크 주소", "광고 표기",
    "조회 환경", "검색 페이지 주소", "조회 시각",
]


class CrawlError(Exception):
    pass


def split_keywords(s: str) -> list[str]:
    found, seen = [], set()
    for word in re.split(r"[\n,]+", s):
        word = word.strip()
        if word and word not in seen:
            seen.add(word)
            found.append(word)
    return found


def safe_excel(v):
    if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + v
    return v


def search_url(keyword: str, device: str) -> str:
    hostname = "m.search.naver.com" if device == "mobile" else "search.naver.com"
    return f"https://{hostname}/search.naver?{urlencode({'query': keyword})}"


def is_naver_search(url: str) -> bool:
    u = urlparse(url)
    return u.scheme == "https" and u.hostname in {"search.naver.com", "m.search.naver.com"}


def check_block(page):
    body = page.locator("body").inner_text(timeout=8000)[:5000]
    phrases = ("자동입력 방지", "보안문자", "비정상적인 접근", "접근이 제한", "접속이 일시적으로 제한")
    if any(p in body for p in phrases):
        raise CrawlError("자동입력 방지 또는 접근 제한 화면이 나타났습니다. 우회하지 않고 수집을 중단합니다.")


def goto_blog_tab(page, device: str, keyword: str, log):
    """검색 화면의 실제 '블로그' 탭을 클릭해서 이동. 실패 시 다른 탭 순위를 가장하지 않음."""
    page.wait_for_timeout(1100)
    # 첫 검색 화면에서 블로그 탭은 대개 상단 메뉴에 존재합니다.
    selectors = ["nav a", '[role="tab"]', '[class*="tab"] a', '[class*="menu"] a', 'a']
    for sel in selectors:
        links = page.locator(sel)
        try:
            n = min(links.count(), 150)
        except Exception:
            continue
        for i in range(n):
            candidate = links.nth(i)
            try:
                if not candidate.is_visible(timeout=300):
                    continue
                if re.sub(r"\s+", "", candidate.inner_text(timeout=300)) != "블로그":
                    continue
                href = (candidate.get_attribute("href") or "").strip()
                if href.startswith("https://blog.naver.com") or href.startswith("https://m.blog.naver.com"):
                    continue
                candidate.click(timeout=4000)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                if not is_naver_search(page.url):
                    raise CrawlError("블로그 탭 클릭 후 예상하지 못한 페이지로 이동했습니다.")
                log("실제 화면의 '블로그' 탭을 클릭했습니다.")
                return "블로그 탭 클릭"
            except CrawlError:
                raise
            except Exception:
                continue
    if device == "desktop":
        log("블로그 탭을 찾지 못해 데스크톱 블로그 검색 주소로 이동합니다. 탭 상태를 확인해주세요.")
        page.goto(f"https://search.naver.com/search.naver?{urlencode({'where':'blog','query':keyword})}", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(900)
        return "데스크톱 where=blog 대체 주소 (탭 검증 필요)"
    raise CrawlError("모바일 검색 화면에서 '블로그' 탭을 찾지 못했습니다. 네이버 화면 구조 확인이 필요합니다. 다른 탭으로 대체하지 않습니다.")


def snapshot(page, extract_js: str) -> list[dict]:
    data = page.evaluate(extract_js)
    if not isinstance(data, list):
        raise CrawlError("화면 추출 결과가 올바르지 않습니다.")
    return data


def _visible_post_ids(page, js: str) -> tuple[str, ...]:
    """Read only rendered post links (never internal search responses)."""
    return tuple(r.get("canonical", "") for r in snapshot(page, js) if r.get("canonical"))


def try_more(page, log, js: str) -> bool:
    """Follow an explicit, visible next/more control on the current search page.

    Never synthesize a page URL or call hidden search APIs. Only accept a click
    when the rendered results or the browser URL change. This avoids mistaking
    an unrelated 'more' button for a new search-results page.
    """
    root = page.locator("#main_pack, #content, main, [role='main']").first
    if not root.count():
        root = page.locator("body")
    selectors = [
        'a.btn_next', 'button.btn_next', 'a.btn_more', 'button.btn_more',
        'a[aria-label="다음 페이지"]', 'button[aria-label="다음 페이지"]',
        'a[aria-label="다음페이지"]', 'button[aria-label="다음페이지"]',
        '[class*="pagination"] a[class*="next"]',
        '[class*="paginate"] a[class*="next"]',
        '[class*="paging"] a[class*="next"]',
        '[class*="pagination"] button[class*="next"]',
        '[class*="more_area"] a', '[class*="more_area"] button',
        '[class*="more_wrap"] a', '[class*="more_wrap"] button',
        'button', 'a',
    ]
    old_url = page.url
    old_ids = _visible_post_ids(page, js)
    examined = set()
    for sel in selectors:
        candidates = root.locator(sel)
        try:
            n = min(candidates.count(), 250)
        except Exception:
            continue
        for i in range(n):
            item = candidates.nth(i)
            try:
                if not item.is_visible(timeout=250) or not item.is_enabled():
                    continue
                # Stable DOM marker prevents double-clicking the same control.
                handle = item.element_handle(timeout=450)
                if handle is None or str(handle) in examined:
                    continue
                examined.add(str(handle))
                info = item.evaluate("""(el) => ({
                  text: (el.innerText || el.textContent || '').replace(/\s+/g,' ').trim(),
                  label: (el.getAttribute('aria-label') || '').trim(),
                  cls: (typeof el.className === 'string' ? el.className : ''),
                  parent: (el.parentElement?.className || '').toString(),
                  href: el.getAttribute('href') || '',
                  nav: !!el.closest('nav, [role="navigation"], [class*="pagination"], [class*="paginate"], [class*="paging"]'),
                  tag: el.tagName.toLowerCase()
                })""")
                name = info['label'] or info['text']
                cls = (info['cls'] + ' ' + info['parent']).lower()
                is_next = name in {'다음', '다음 페이지', '다음페이지', '다음 10개', '다음 검색결과'}
                is_more = name in {'더보기', '결과 더보기', '검색결과 더보기', '블로그 더보기', '더 보기'}
                likely_control = (('next' in cls or 'paging' in cls or 'paginate' in cls) and is_next) or (('more' in cls) and is_more)
                if not (likely_control or (info['nav'] and is_next) or (info['tag'] == 'button' and is_more)):
                    continue
                if info['href'].lower().startswith(('javascript:', 'data:')):
                    continue
                item.click(timeout=3000)
                for _ in range(3):
                    page.wait_for_timeout(900)
                    if not is_naver_search(page.url):
                        raise CrawlError('결과 더보기 후 네이버 검색 페이지를 벗어났습니다.')
                    new_ids = _visible_post_ids(page, js)
                    if (page.url != old_url and new_ids) or any(post not in old_ids for post in new_ids):
                        log(f"실제 화면의 '{name}' 버튼으로 다음 검색 결과를 불러왔습니다.")
                        return True
                log(f"'{name}' 버튼을 눌렀지만 추가 게시글이 나타나지 않았습니다.")
                return False
            except CrawlError:
                raise
            except Exception:
                continue
    return False


def capture_debug(page, debug_dir: Path, keyword: str, label: str, log) -> str:
    debug_dir.mkdir(parents=True, exist_ok=True)
    stub = re.sub(r"[^\w가-힣-]+", "_", keyword)[:35].strip("_") or "keyword"
    target = debug_dir / f"{datetime.now():%H%M%S}_{stub}_{label}.png"
    try:
        page.screenshot(path=str(target), full_page=False, timeout=8000)
        log(f"화면 증빙 저장: {target}")
        return str(target)
    except Exception as ex:
        log(f"화면 저장 실패: {ex}")
        return ""


def crawl_keyword(page, keyword: str, device: str, limit: int, max_scrolls: int,
                  pause: float, stop: threading.Event, log, debug_dir: Path,
                  save_screenshots: bool = False) -> tuple[list[dict], dict]:
    """Collect genuinely rendered Naver blog items, in their first-seen order.

    The request count is a ceiling, not a promise of availability. If the site
    reveals fewer results, return the observed count with an explicit reason.
    """
    page.goto(search_url(keyword, device), wait_until="domcontentloaded", timeout=35000)
    check_block(page)
    if stop.is_set():
        return [], {"keyword": keyword, "count": 0, "status": "중지", "url": page.url,
                    "mode": "", "reason": "사용자 중지", "pages": 0}
    method = goto_blog_tab(page, device, keyword, log)
    check_block(page)
    if save_screenshots:
        capture_debug(page, debug_dir, keyword, "첫화면", log)
    js = JS_FILE.read_text(encoding="utf-8")
    found: dict[str, dict] = {}
    stagnant = 0
    page_moves = 0
    reason = "최대 스크롤 횟수에 도달했습니다."
    for index in range(max_scrolls + 1):
        if stop.is_set():
            reason = "사용자 중지"
            break
        check_block(page)
        old_size = len(found)
        for rec in snapshot(page, js):
            if rec.get("canonical") and rec["canonical"] not in found:
                found[rec["canonical"]] = rec
        log(f"'{keyword}' 화면 확인 {index + 1}/{max_scrolls + 1}, 고유 게시글 {len(found)}/{limit}건, 페이지 이동 {page_moves}회")
        if len(found) >= limit:
            reason = "설정한 수집 건수에 도달했습니다."
            break
        stagnant = stagnant + 1 if len(found) == old_size else 0
        if index >= max_scrolls:
            break
        at_bottom = page.evaluate("""() =>
            window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 100""")
        if stagnant >= 2 and at_bottom:
            if try_more(page, log, js):
                page_moves += 1
                stagnant = 0
                if stop.wait(max(1.0, pause)):
                    reason = "사용자 중지"
                    break
                continue
            reason = "화면 하단에서 추가 게시글 또는 다음 페이지를 찾지 못했습니다."
            log(f"'{keyword}' 검색화면 결과 소진: {len(found)}/{limit}건, 다음 페이지를 확인하지 못함")
            break
        if stagnant >= 4:
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            log("새 게시글이 없어 검색결과 맨 아래까지 이동해 다시 확인합니다.")
        else:
            page.evaluate("window.scrollBy(0, Math.max(650, Math.floor(window.innerHeight * 0.90)))")
        if stop.wait(pause):
            reason = "사용자 중지"
            break
    if not found and not stop.is_set():
        capture_debug(page, debug_dir, keyword, "수집실패", log)
        raise CrawlError("게시글 링크를 읽지 못했습니다. 네이버 페이지 구조 변경, 검색 결과 없음 또는 접근 제한 가능성이 있습니다. 오류 화면을 확인해주세요.")
    if len(found) < limit and save_screenshots:
        capture_debug(page, debug_dir, keyword, "종료화면", log)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for pos, rec in enumerate(list(found.values())[:limit], start=1):
        rows.append({
            "검색 키워드": keyword, "화면 수집 순번": pos,
            "게시글 제목": rec.get("title", ""), "블로그명": rec.get("blogName", ""),
            "게시일(화면 표기)": rec.get("published", ""),
            "검색화면 요약": rec.get("description", ""), "블로그 ID": rec.get("blogId", ""),
            "게시글 주소": rec["canonical"], "화면 링크 주소": rec.get("href", ""),
            "광고 표기": rec.get("isAd", ""), "조회 환경": "모바일 웹 재현" if device == "mobile" else "데스크톱 브라우저",
            "검색 페이지 주소": page.url, "조회 시각": timestamp,
        })
    log(f"'{keyword}' 수집 종료: {len(rows)}/{limit}건, 페이지 이동 {page_moves}회. 종료 사유: {reason}")
    summary = {"keyword": keyword, "count": len(rows),
               "status": "중지" if stop.is_set() else "정상" if len(rows) >= limit else "화면에서 추가 결과 미발견",
               "url": page.url, "mode": method, "reason": reason, "pages": page_moves}
    return rows, summary


def save_excel(dest: Path, records: list[dict], reports: list[dict], config: dict):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    result = wb.active
    result.title = "화면 수집 결과"
    result.append(COLUMNS)
    for record in records:
        result.append([safe_excel(record.get(header, "")) for header in COLUMNS])
    formats = [24, 18, 65, 25, 21, 75, 23, 58, 58, 16, 23, 58, 22]

    def style(ws, widths):
        ws.freeze_panes = "C2" if ws == result else "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.row_dimensions[1].height = 29
        for col, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        for c in ws[1]:
            c.fill = PatternFill("solid", fgColor="17365D")
            c.font = Font(name="맑은 고딕", size=10, color="FFFFFF", bold=True)
            c.alignment = Alignment(horizontal="center", vertical="center")
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font = Font(name="맑은 고딕", size=10)
                cell.alignment = Alignment(vertical="center")
                if cell.row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F3F6FA")
    style(result, formats)
    for i in range(2, result.max_row + 1):
        for col in (8, 9, 12):
            c = result.cell(i, col)
            if str(c.value).startswith("https://"):
                c.hyperlink = c.value
                c.font = Font(name="맑은 고딕", size=10, color="1565C0", underline="single")

    summary = wb.create_sheet("키워드별 현황")
    summary.append(["검색 키워드", "수집 게시글 수", "수집 상태", "실제 조회된 화면 주소", "탭 선택 방식", "오류 내용", "종료 사유", "페이지 이동 횟수"])
    for row in reports:
        summary.append([safe_excel(row.get(k, "")) for k in ("keyword", "count", "status", "url", "mode", "error", "reason", "pages")])
    style(summary, [27, 20, 25, 75, 46, 60, 90, 20])

    note = wb.create_sheet("수집 기준 안내")
    note.append(["항목", "설명"])
    details = [
        ("방식", "Playwright가 실제 네이버 검색 페이지를 브라우저로 연 뒤, 렌더링된 게시글 링크와 주변 표시 텍스트를 읽습니다. 네이버 공식 검색 API는 전혀 사용하지 않습니다."),
        ("조회 환경", "모바일은 Chromium 모바일 기기 화면 재현이며, 네이버 앱 실기기 또는 특정 사용자 로그인 상태와 완전히 같지는 않습니다."),
        ("화면 수집 순번", "열린 검색 결과 화면에서 위에서 아래로 발견된 네이버 블로그 게시글의 고유 URL 기준 번호입니다. 광고, 다른 종류의 콘텐츠, 화면 구조 변화에 따라 전체 화면상의 절대 순위를 뜻하지 않습니다."),
        ("화면 범위", "블로그 탭에서 렌더링된 콘텐츠만 수집합니다. 숨겨진 전체 검색 결과나 API 제공 결과를 의미하지 않습니다."),
        ("자료 내용", "제목, 작성자, 날짜 및 요약을 검색화면에서 수집합니다. 블로그명이 비어 있다면 옵션 선택 시 해당 블로그 공개 화면을 열어 표시된 이름을 추가 확인합니다. 본문 전체는 수집하지 않습니다."),
        ("블로그명 표기", "검색화면의 블로그명 또는 작성자명(별명)을 기록합니다. 없는 경우 실제 블로그 공개 화면에 표시된 프로필명을 확인합니다. 확인할 수 없는 이름을 블로그 ID로 임의 대체하지 않습니다."),
        ("날짜", "상대적 날짜('3일 전' 등)는 그대로 저장되며 게시글의 실제 발행일로 추정 변환하지 않습니다."),
        ("결과 변동", "네이버 검색 결과는 시점, 기기, 로그인 상태, 개인화와 검색화면 구성에 따라 달라질 수 있습니다."),
        ("중복 처리", "동일 검색 키워드 내 블로그 ID+게시글 ID가 같으면 한 번만 수집하고, 서로 다른 키워드의 중복은 유지합니다."),
        ("정렬", "검색화면의 기본 정렬을 사용합니다. 프로그램은 API 정렬 파라미터를 사용하지 않습니다."),
        ("접근 제한", "자동입력 방지 화면, 접근 제한이 나타나면 우회하지 않습니다. 반복 대량 실행을 피하고 서비스 정책을 확인하세요."),
        ("수집 설정", f"검색 모드: {config['device']} / 키워드별 상한: {config['limit']} / 최대 스크롤: {config['scrolls']} / 스크롤 대기: {config['pause']}초"),
        ("설정 건수와 실제 수집", "설정한 100/200/300건은 최대 수집 건수이지 보장된 네이버 검색 결과 수가 아닙니다. 페이지에서 확인된 게시글만 수집하며 중단된 이유는 키워드별 현황에 기록합니다."),
        ("생성 시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    for a, b in details:
        note.append([a, b])
    style(note, [25, 120])
    for row in note.iter_rows(min_row=2):
        row[1].alignment = Alignment(vertical="center", wrap_text=True)
        note.row_dimensions[row[0].row].height = 40
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)


def fill_missing_blog_names(context, records: list[dict], stop: threading.Event, log, pause: float):
    """검색 화면에서 작성자명을 못 찾은 경우, 실제 공개 블로그 화면에서 보완 수집.

    HTTP API/검색 API를 호출하지 않고 Chromium에서 화면에 표시된 DOM만 확인합니다.
    동일 블로그 ID는 한번만 열고, 실패하면 빈칸으로 남깁니다.
    """
    missing = [r for r in records if not r.get("블로그명") and r.get("블로그 ID")]
    if not missing or stop.is_set():
        return
    ids = list(dict.fromkeys(r["블로그 ID"] for r in missing))
    log(f"검색화면에서 블로그명 누락 {len(missing)}건 / 블로그 {len(ids)}개 화면 추가 확인 시작")
    page = context.new_page()
    found: dict[str, str] = {}
    js = BLOG_NAME_JS_FILE.read_text(encoding="utf-8")
    try:
        for index, blog_id in enumerate(ids, 1):
            if stop.is_set():
                break
            try:
                # 검색 결과에서 이미 검증된 네이버 블로그 ID만 URL에 사용합니다.
                if not re.fullmatch(r"[a-zA-Z0-9_-]{2,70}", blog_id):
                    continue
                page.goto(f"https://m.blog.naver.com/{blog_id}",
                          wait_until="domcontentloaded", timeout=16000)
                if urlparse(page.url).hostname not in {"m.blog.naver.com", "blog.naver.com"}:
                    continue
                check_block(page)
                page.wait_for_timeout(850)
                name = page.evaluate(js)
                if not name:
                    page.wait_for_timeout(1000)
                    name = page.evaluate(js)
                if isinstance(name, str) and name.strip():
                    found[blog_id] = name.strip()
                    log(f"[{index}/{len(ids)}] '{blog_id}' 블로그명 확인: {name.strip()}")
                else:
                    log(f"[{index}/{len(ids)}] '{blog_id}' 블로그명 화면 확인 불가 (빈칸 유지)")
            except CrawlError as exc:
                log(f"'{blog_id}': {exc}")
                if "접근 제한" in str(exc) or "자동입력" in str(exc):
                    break
            except Exception as exc:
                log(f"'{blog_id}' 블로그 화면 조회 실패: {type(exc).__name__} (빈칸 유지)")
            if index < len(ids) and stop.wait(max(0.8, min(pause, 3.0))):
                break
    finally:
        page.close()
    for row in missing:
        if row["블로그 ID"] in found:
            row["블로그명"] = found[row["블로그 ID"]]
    total = sum(bool(row.get("블로그명")) for row in records)
    log(f"블로그명 최종 확보 {total}/{len(records)}건 (확인 불가는 빈칸)")


def run_batch(config: dict, stop: threading.Event, log):
    from playwright.sync_api import sync_playwright
    records, reports = [], []
    debug_dir = Path(config["dest"]).parent / f"네이버_수집_진단화면_{datetime.now():%Y%m%d_%H%M%S}"
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=config.get("headless", False), channel="chrome", args=["--no-sandbox"] if config.get("headless", False) else [])
            log("Google Chrome 브라우저가 시작되었습니다.")
        except Exception:
            try:
                browser = pw.chromium.launch(headless=config.get("headless", False), executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH") or None, args=["--no-sandbox"] if config.get("headless", False) else [])
                log("Playwright Chromium 브라우저가 시작되었습니다.")
            except Exception as exc:
                raise CrawlError("브라우저를 실행할 수 없습니다. 시작 배치파일을 실행해 Playwright Chromium을 설치해주세요.") from exc
        try:
            if config["device"] == "mobile":
                device = dict(pw.devices["Pixel 7"] if "Pixel 7" in pw.devices else pw.devices["iPhone 13"])
                device.pop("default_browser_type", None)
                context = browser.new_context(**device, locale="ko-KR", timezone_id="Asia/Seoul")
            else:
                context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR", timezone_id="Asia/Seoul")
            try:
                page = context.new_page()
                for n, keyword in enumerate(config["keywords"], start=1):
                    if stop.is_set():
                        break
                    log(f"[{n}/{len(config['keywords'])}] '{keyword}' 검색 중...")
                    try:
                        rows, summary = crawl_keyword(page, keyword, config["device"], config["limit"],
                            config["scrolls"], config["pause"], stop, log, debug_dir, config.get("screenshots", False))
                        records.extend(rows)
                        reports.append(summary)
                    except Exception as exc:
                        if isinstance(exc, CrawlError):
                            message = str(exc)
                        else:
                            message = f"조회 오류: {type(exc).__name__}: {exc}"
                        log(f"⚠ '{keyword}': {message}")
                        reports.append({"keyword": keyword, "count": 0, "status": "오류", "url": page.url, "mode": "", "error": message[:400]})
                        try:
                            capture_debug(page, debug_dir, keyword, "오류", log)
                        except Exception:
                            pass
                        if "접근 제한" in message or "자동입력" in message:
                            log("접근 제한 감지로 남은 키워드 수집을 종료합니다.")
                            break
                    if n < len(config["keywords"]) and stop.wait(max(2.0, config["pause"])):
                        break
                named = sum(bool(row.get("블로그명")) for row in records)
                log(f"검색화면에서 확인한 블로그명 {named}/{len(records)}건")
                if config.get("lookup_missing_names", True):
                    fill_missing_blog_names(context, records, stop, log, config["pause"])
            finally:
                context.close()
        finally:
            browser.close()
    save_excel(Path(config["dest"]), records, reports, config)
    return len(records), len([r for r in reports if r["status"] == "오류"]), debug_dir if debug_dir.exists() else None


def start_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    class App:
        def __init__(self):
            self.root = tk.Tk()
            self.root.title(APP_TITLE)
            self.root.geometry("790x710")
            self.root.minsize(690, 620)
            self.messages = queue.Queue()
            self.stop = threading.Event()
            self.running = False
            wrap = ttk.Frame(self.root, padding=20)
            wrap.pack(fill="both", expand=True)
            ttk.Label(wrap, text="🔎 네이버 블로그 검색화면 수집기", font=("맑은 고딕", 17, "bold")).pack(anchor="w")
            ttk.Label(wrap, text="실제 검색 페이지를 브라우저로 열어 수집합니다. API 키 불필요 / 모바일 웹 기준 기본 설정",
                      foreground="#53657A").pack(anchor="w", pady=(5, 14))
            group = ttk.LabelFrame(wrap, text="1. 검색 조건", padding=12)
            group.pack(fill="both", expand=True)
            ttk.Label(group, text="검색할 키워드 (한 줄에 하나씩 입력)").grid(row=0, column=0, columnspan=4, sticky="w")
            self.keywords = tk.Text(group, height=6, font=("맑은 고딕", 11), undo=True)
            self.keywords.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(5, 12))
            self.keywords.insert("1.0", "아넬라 퓨레\n로지오가닉 배도라지")
            ttk.Label(group, text="조회 환경").grid(row=2, column=0, sticky="w", pady=5)
            self.device = tk.StringVar(value="모바일 웹")
            ttk.Combobox(group, textvariable=self.device, state="readonly", width=16,
                         values=["모바일 웹", "데스크톱"]).grid(row=2, column=1, sticky="w", pady=5)
            ttk.Label(group, text="키워드별 최대 건수").grid(row=2, column=2, sticky="w", pady=5)
            self.limit = tk.StringVar(value="50")
            ttk.Spinbox(group, from_=1, to=200, textvariable=self.limit, width=10).grid(row=2, column=3, sticky="w", pady=5)
            ttk.Label(group, text="최대 스크롤 횟수").grid(row=3, column=0, sticky="w", pady=5)
            self.scrolls = tk.StringVar(value="15")
            ttk.Spinbox(group, from_=0, to=80, textvariable=self.scrolls, width=10).grid(row=3, column=1, sticky="w")
            ttk.Label(group, text="스크롤 간 대기(초)").grid(row=3, column=2, sticky="w", pady=5)
            self.pause = tk.StringVar(value="1.5")
            ttk.Entry(group, textvariable=self.pause, width=12).grid(row=3, column=3, sticky="w")
            self.screenshots = tk.BooleanVar(value=False)
            ttk.Checkbutton(group, text="키워드별 첫 화면 스크린샷도 저장", variable=self.screenshots).grid(
                row=4, column=0, columnspan=4, sticky="w", pady=(5, 0))
            self.lookup_missing_names = tk.BooleanVar(value=True)
            ttk.Checkbutton(group, text="블로그명 누락 시 실제 블로그 화면에서 추가 확인 (시간 소요)",
                            variable=self.lookup_missing_names).grid(row=5, column=0, columnspan=4, sticky="w", pady=(5, 0))
            ttk.Label(group, text="※ 화면 순서는 수집된 블로그 글 기준입니다. 개인화/광고 등으로 전체 검색의 절대 순위와 다를 수 있습니다.",
                      foreground="#65758B", wraplength=680).grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 2))
            group.columnconfigure(1, weight=1)
            group.columnconfigure(3, weight=1)
            group.rowconfigure(1, weight=1)
            actions = ttk.Frame(wrap)
            actions.pack(fill="x", pady=12)
            self.start_button = ttk.Button(actions, text="실제 화면 수집 → 엑셀 저장", command=self.start)
            self.start_button.pack(side="left")
            self.stop_button = ttk.Button(actions, text="중지 및 수집분 저장", command=self.request_stop, state="disabled")
            self.stop_button.pack(side="left", padx=10)
            self.progress = ttk.Progressbar(actions, mode="indeterminate")
            self.progress.pack(side="right", fill="x", expand=True, padx=(10, 0))
            logs = ttk.LabelFrame(wrap, text="2. 진행 기록", padding=10)
            logs.pack(fill="both", expand=True)
            self.log_area = tk.Text(logs, height=10, wrap="word", font=("맑은 고딕", 10), state="disabled")
            self.log_area.pack(fill="both", expand=True)
            self.log("V2.2 준비 완료. 블로그명 추출 기능 개선 / 네이버 API 미사용")
            self.root.after(150, self.poll)

        def log(self, line):
            self.log_area.configure(state="normal")
            self.log_area.insert("end", f"[{datetime.now():%H:%M:%S}] {line}\n")
            self.log_area.see("end")
            self.log_area.configure(state="disabled")

        def start(self):
            if self.running:
                return
            words = split_keywords(self.keywords.get("1.0", "end"))
            try:
                limit, scrolls, pause = int(self.limit.get()), int(self.scrolls.get()), float(self.pause.get())
                if not words or not (1 <= limit <= 200) or not (0 <= scrolls <= 80) or not (0.8 <= pause <= 30.0):
                    raise ValueError("키워드를 입력하고 건수(1~200), 스크롤(0~80), 대기(0.8~30초)를 확인해주세요.")
            except ValueError as exc:
                messagebox.showwarning("입력 확인", str(exc))
                return
            filename = f"네이버_실제화면_블로그_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
            dest = filedialog.asksaveasfilename(title="결과 엑셀 저장", defaultextension=".xlsx",
                initialfile=filename, filetypes=[("Excel 통합문서", "*.xlsx")])
            if not dest:
                return
            config = {"keywords": words, "device": "mobile" if self.device.get() == "모바일 웹" else "desktop",
                      "limit": limit, "scrolls": scrolls, "pause": pause, "screenshots": self.screenshots.get(),
                      "lookup_missing_names": self.lookup_missing_names.get(), "dest": dest}
            self.running = True
            self.stop.clear()
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.progress.start(10)
            self.log(f"총 {len(words)}개 키워드 수집 시작 / {self.device.get()} / API 미사용")
            threading.Thread(target=self.worker, args=(config,), daemon=True).start()

        def worker(self, cfg):
            try:
                result = run_batch(cfg, self.stop, lambda msg: self.messages.put(("log", msg)))
                self.messages.put(("done", (cfg["dest"], *result)))
            except Exception as e:
                self.messages.put(("error", str(e)))

        def request_stop(self):
            self.stop.set()
            self.log("중지 요청. 현재까지 수집된 결과를 엑셀에 저장합니다.")

        def poll(self):
            try:
                while True:
                    kind, payload = self.messages.get_nowait()
                    if kind == "log":
                        self.log(payload)
                    elif kind == "done":
                        dest, count, errors, debug_dir = payload
                        self.finish()
                        self.log(f"저장 완료: {dest} / {count}건 / 오류 키워드 {errors}개")
                        messagebox.showinfo("수집 완료", f"수집 {count}건 / 오류 키워드 {errors}개\n\n엑셀: {dest}" +
                                            (f"\n\n오류 화면 폴더: {debug_dir}" if debug_dir else ""))
                    else:
                        self.finish()
                        self.log(f"실행 오류: {payload}")
                        messagebox.showerror("실행 오류", str(payload))
            except queue.Empty:
                pass
            self.root.after(150, self.poll)

        def finish(self):
            self.running = False
            self.progress.stop()
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")

    root = App()
    root.root.mainloop()


def main():
    p = argparse.ArgumentParser(description=APP_TITLE)
    p.add_argument("--cli", action="store_true", help="명령줄 환경에서 수집")
    p.add_argument("--keywords", default="", help="쉼표로 구분한 키워드")
    p.add_argument("--device", choices=["mobile", "desktop"], default="mobile")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--scrolls", type=int, default=15)
    p.add_argument("--pause", type=float, default=1.5)
    p.add_argument("--screenshots", action="store_true")
    p.add_argument("--output", default=f"네이버_실제화면_블로그_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    args = p.parse_args()
    if not args.cli:
        start_gui()
        return
    keywords = split_keywords(args.keywords)
    if not keywords or not (1 <= args.limit <= 200) or not (0 <= args.scrolls <= 80) or not (0.8 <= args.pause <= 30):
        p.error("키워드와 조회 수/스크롤/대기시간을 확인해주세요.")
    cfg = {"keywords": keywords, "device": args.device, "limit": args.limit,
           "scrolls": args.scrolls, "pause": args.pause,
           "screenshots": args.screenshots, "dest": str(Path(args.output).absolute())}
    count, errors, debug_dir = run_batch(cfg, threading.Event(), print)
    print(f"저장 완료: {cfg['dest']} / {count}건 / 키워드별 오류 {errors}개")
    if debug_dir:
        print(f"진단 화면: {debug_dir}")
    if errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
