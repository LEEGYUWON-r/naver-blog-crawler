() => {
  // 검색화면에서 블로그명을 찾지 못했을 때, 해당 블로그의 실제 공개 화면을 확인하는 보완 로직.
  // 메타데이터/내부 API/임의의 블로그 ID 치환은 사용하지 않습니다.
  const visible = (e) => !!(e && e.isConnected && e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none');
  const txt = (e) => (e?.innerText || '').replace(/\s+/g, ' ').trim();
  const bad = (s) => !s || s.length < 2 || s.length > 65 ||
    /^(?:네이버 블로그|블로그|NAVER|글쓰기|전체보기|카테고리|프로필|이웃추가|서로이웃|팔로우|방문자|검색|닫기|이전|홈)$/.test(s);
  // 닉네임/작성자 표기를 블로그의 표제보다 우선합니다.
  const selectors = [
    '#nickName', '#nickname', '.nick_name', '.nickname', '[class*="nick_name"]',
    '[class*="nickname"]', '.profile_name', '[class*="profile_name"]',
    '[class*="blogger_name"]', '[class*="author_name"]',
    '.blog_name', '[class*="blog_name"]', '#blogTitle', '[class*="blog_title"]',
    '.profile .name', '[class*="profile"] [class*="name"]',
    'header h1', 'header h2'
  ];
  for (const selector of selectors) {
    for (const e of document.querySelectorAll(selector)) {
      if (!visible(e)) continue;
      const s = txt(e);
      if (!bad(s)) return s;
    }
  }
  return '';
}
