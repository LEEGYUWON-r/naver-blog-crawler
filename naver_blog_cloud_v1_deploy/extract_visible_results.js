() => {
  // API나 숨겨진 검색 응답은 사용하지 않고, 실제 화면에 나타난 DOM만 읽습니다.
  const root = document.querySelector('#main_pack') || document.querySelector('#content') ||
               document.querySelector('[role="main"]') || document.querySelector('main') || document.body;
  const txt = (e) => (e?.innerText || e?.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = (e) => !!(e && e.isConnected && e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none');
  const cls = (e) => typeof e?.className === 'string' ? e.className : (e?.className?.baseVal || '');
  const canonical = (raw, depth = 0) => {
    try {
      const u = new URL(raw, location.href);
      const host = u.hostname.toLowerCase();
      if (depth === 0 && ['search.naver.com', 'm.search.naver.com'].includes(host)) {
        const nested = ['url','u','target','dest'].map(k => u.searchParams.get(k)).find(Boolean);
        return nested ? canonical(nested, 1) : null;
      }
      if (depth > 1 || !['blog.naver.com', 'm.blog.naver.com', 'blog.me', 'www.blog.me'].includes(host)) return null;
      const p = u.pathname.split('/').filter(Boolean);
      let user = '', post = '';
      if (host.endsWith('blog.me')) {
        if (p.length >= 2 && /^\d{6,}$/.test(p[1])) { user = p[0]; post = p[1]; }
      } else if (p[0]?.toLowerCase() === 'postview.naver') {
        user = u.searchParams.get('blogId') || u.searchParams.get('blogid') || '';
        post = u.searchParams.get('logNo') || u.searchParams.get('logno') || '';
      } else if (p.length >= 2) { user = p[0]; post = p[1]; }
      if (!/^[a-zA-Z0-9_-]{2,70}$/.test(user) || !/^\d{6,20}$/.test(post)) return null;
      return {canonical: `https://m.blog.naver.com/${user}/${post}`, blogId: user, postId: post};
    } catch { return null; }
  };
  const profileLink = (a, id) => {
    if (!id) return false;
    try {
      const u = new URL(a.href, location.href), p = u.pathname.split('/').filter(Boolean);
      if (!['blog.naver.com', 'm.blog.naver.com'].includes(u.hostname.toLowerCase())) return false;
      return (p.length === 1 && p[0].toLowerCase() === id.toLowerCase()) ||
             (p[0]?.toLowerCase() === 'blog.naver' && (u.searchParams.get('blogId') || '').toLowerCase() === id.toLowerCase());
    } catch { return false; }
  };
  const badName = (s, title) => !s || s === title || s.length > 65 ||
    /^(?:블로그|네이버|NAVER|프로필|이웃추가|서로이웃|공감|더보기|팔로우|광고|작성자|방문하기|전체보기|블로그홈|목록|카테고리)$/.test(s) ||
    /^\d+\s*(?:시간|일|주|개월|년)\s*전$/.test(s) || /^20\d\d[.\/-]/.test(s);
  const chooseName = (card, id, title) => {
    // 1. 같은 블로그 ID로 연결되는 작성자/프로필 링크가 가장 신뢰도 높음.
    for (const a of card.querySelectorAll('a[href]')) {
      if (!visible(a) || !profileLink(a, id)) continue;
      const s = txt(a) || (a.getAttribute('aria-label') || '').trim();
      if (!badName(s, title)) return s;
    }
    // 2. 모바일/PC 검색 UI의 다양한 작성자 명칭.
    const selectors = [
      '.user_name', '.sub_name', '.blog_name', '.profile_name', '.nick_name', '.nickname',
      '.name_text', '.author_name', '.writer_name', '.blogger_name',
      '[class*="user_name"]', '[class*="sub_name"]', '[class*="blog_name"]',
      '[class*="nick"]', '[class*="author_name"]', '[class*="writer_name"]',
      '[class*="user_info"] a', '[class*="profile"] a', '[class*="user_info"] [class*="name"]',
      '[class*="profile"] [class*="name"]',
      '.user_info .name', '.user_info .user', '.name'
    ];
    for (const selector of selectors) {
      for (const e of card.querySelectorAll(selector)) {
        if (!visible(e)) continue;
        const s = txt(e);
        if (!badName(s, title)) return s;
      }
    }
    return '';
  };
  const blogUrlCount = (node) => {
    const set = new Set();
    for (const a of node.querySelectorAll('a[href]')) {
      const c = canonical(a.href);
      if (c) set.add(c.canonical);
      if (set.size > 2) break;
    }
    return set.size;
  };
  const findCard = (link, blogId, title) => {
    let cur = link.parentElement, best = cur, bestScore = -1;
    for (let depth = 0; cur && cur !== root && depth < 11; depth++, cur = cur.parentElement) {
      const content = txt(cur);
      if (content.length > 3800 || blogUrlCount(cur) > 1) break;
      const name = chooseName(cur, blogId, title);
      const hasDescription = !!cur.querySelector('[class*="desc"], [class*="dsc"], [class*="summary"], [class*="snippet"]');
      const boundary = cur.matches('li, article, [class*="view_wrap"], [class*="total_wrap"], [class*="result_item"], [class*="view_item"], [class*="item_bx"], [class*="content_bx"]');
      const score = (name ? 1000 : 0) + (hasDescription ? 40 : 0) + (boundary ? 20 : 0) + depth;
      if (score >= bestScore) { best = cur; bestScore = score; }
      if (name && boundary) break;
    }
    return best || link.parentElement;
  };
  const pickDescription = (card, title) => {
    const selectors = ['.dsc_txt', '[class*="desc_txt"]', '[class*="summary"]', '[class*="snippet"]',
      '[class*="desc"]', '[class*="detail_txt"]', '.text_type_body'];
    for (const selector of selectors) {
      for (const e of card.querySelectorAll(selector)) {
        if (!visible(e)) continue;
        const s = txt(e);
        if (s && s !== title && s.length > 10 && s.length < 800) return s;
      }
    }
    return '';
  };
  const pickDate = (card) => {
    const pattern = /(?:20\d{2}[.\/-]\s?\d{1,2}[.\/-]\s?\d{1,2}\.?|\d+\s?(?:시간|일|주|개월|년)\s?전)/;
    for (const selector of ['.sub_time', 'time', '[class*="sub_time"]', '[class*="date"]', '[class*="time"]']) {
      for (const e of card.querySelectorAll(selector)) {
        if (!visible(e)) continue;
        const m = txt(e).match(pattern);
        if (m) return m[0];
      }
    }
    const m = txt(card).match(pattern);
    return m ? m[0] : '';
  };
  const isTitleLink = (a) => {
    const s = [a, a.parentElement, a.parentElement?.parentElement].map(cls).join(' ');
    return /(?:^|[\s_-])(?:title|tit|subject|headline|total_tit|title_link)(?:$|[\s_-])/i.test(s) ||
           !!a.closest('h2,h3,h4, [class*="title_area"], [class*="tit_area"], [class*="title_wrap"]');
  };
  const anchorScore = (a, title) => {
    let s = 0;
    if (isTitleLink(a)) s += 300;
    if (a.closest('[class*="desc"],[class*="dsc"],[class*="summary"],[class*="snippet"]')) s -= 250;
    if (a.closest('h2,h3,h4')) s += 110;
    if (title.length > 140) s -= 140;
    if (title.length > 100) s -= 35;
    if (/\.{3}$|…$/.test(title)) s -= 15;
    s += Math.min(title.length, 70) / 30;
    return s;
  };
  const grouped = new Map();
  let order = 0;
  for (const a of root.querySelectorAll('a[href]')) {
    if (!visible(a)) continue;
    const url = canonical(a.href);
    if (!url) continue;
    const title = txt(a);
    if (title.length < 4 || title.length > 600 || /^(?:블로그|더보기|원문보기|게시물|프로필|공유|이웃추가|공감|방문하기|전체보기)$/.test(title)) continue;
    const score = anchorScore(a, title);
    const item = { a, url, title, score, order: order++ };
    const prev = grouped.get(url.canonical);
    if (!prev || item.score > prev.score) grouped.set(url.canonical, item);
  }
  const results = [];
  for (const cand of grouped.values()) {
    const {a, url, title, score, order} = cand;
    const card = findCard(a, url.blogId, title);
    const author = chooseName(card, url.blogId, title);
    const box = a.getBoundingClientRect();
    const cardText = txt(card).slice(0, 450);
    const ad = /(?:^|\s)(?:광고|Sponsored)(?:\s|$)/i.test(cardText);
    results.push({
      ...url, title, blogName: author, published: pickDate(card),
      description: pickDescription(card, title), href: a.href, isAd: ad ? '광고 표기 감지' : '',
      y: Math.round(box.top + window.scrollY), x: Math.round(box.left), score, order
    });
  }
  results.sort((a, b) => a.y - b.y || a.x - b.x || a.order - b.order);
  return results;
}
