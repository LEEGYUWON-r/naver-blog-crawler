from __future__ import annotations
import importlib
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from screen_crawler import COLUMNS, save_excel


def test_real_chromium_dom_extraction():
    """실제 로컬 Chromium과 JS가 제목, 작성자, URL, 화면 순서를 읽는지 검증."""
    from screen_crawler import JS_FILE
    html = '''<html><body><main id="main_pack">
    <ul><li class="result_item"><div class="user_info"><a href="https://m.blog.naver.com/fruitmom">과일맘의 기록</a></div>
    <a class="title_link" href="https://m.blog.naver.com/fruitmom/1234567890">아기첫과일 선택 후기</a>
    <div class="dsc_txt">처음 먹이는 과일 제품들을 비교해봤어요.</div><span class="sub_time">2026.10.13.</span></li>
    <li class="result_item"><div class="user_info"><a href="https://m.blog.naver.com/lovelykid">행복육아</a></div>
    <a class="title_link" href="https://m.blog.naver.com/lovelykid/1234567891">아기 과일 시작했어요</a>
    <div class="dsc_txt">이 글은 두 번째 게시글의 요약입니다.</div><span class="sub_time">3일 전</span></li></ul></main></body></html>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.set_content(html)
            got = page.evaluate(JS_FILE.read_text(encoding='utf-8'))
            assert len(got) == 2, got
            assert [x['blogName'] for x in got] == ['과일맘의 기록', '행복육아']
            assert [x['title'] for x in got] == ['아기첫과일 선택 후기', '아기 과일 시작했어요']
            assert got[0]['canonical'] == 'https://m.blog.naver.com/fruitmom/1234567890'
        finally:
            browser.close()


def test_end_to_end_http_mock_crawl(tmp_path):
    os.environ['CRAWLER_DATA_DIR'] = str(tmp_path)
    import app as mod
    mod.DATA_DIR = tmp_path
    mod.ACCESS_KEY='test-secret'
    mod.REQUIRE_KEY=True
    client = TestClient(mod.app)
    hdr={'X-Access-Key':'test-secret'}
    assert client.get('/health').status_code == 200
    assert client.get('/api/auth-check').status_code == 401
    assert client.get('/api/auth-check',headers={'X-Access-Key':'wrong-code'}).status_code == 401
    assert client.get('/api/auth-check',headers=hdr).json() == {'ok':True}
    assert client.get('/').status_code == 200
    assert client.post('/api/jobs', json={'keywords':'아기첫과일'}).status_code == 401
    assert client.post('/api/jobs',json={'keywords':'\n'.join(str(i) for i in range(9))},headers=hdr).status_code == 422

    def fake_crawl(config,stop,log):
        log("테스트 브라우저 수집 중")
        record={k:'' for k in COLUMNS}
        record.update({'검색 키워드':config['keywords'][0],'화면 수집 순번':1,'게시글 제목':'아기첫과일 선택 후기',
                       '블로그명':'과일맘의 기록','게시글 주소':'https://m.blog.naver.com/fruitmom/1234567890'})
        save_excel(Path(config['dest']), [record], [{'keyword':config['keywords'][0],'count':1,'status':'정상','url':'https://m.search.naver.com/search.naver','mode':'블로그 탭 클릭'}], config)
        return 1,0,None
    with patch.object(mod,'run_batch',fake_crawl):
        response=client.post('/api/jobs',json={'keywords':'아기첫과일','device':'mobile'},headers=hdr)
        assert response.status_code==202, response.text
        jid=response.json()['id']
        for _ in range(60):
            result=client.get(f'/api/jobs/{jid}',headers=hdr)
            assert result.status_code==200
            state=result.json()
            if state['status'] in ('완료','오류','부분 실패','중지'): break
            time.sleep(.1)
        assert state['status']=='완료',state
        assert state['rows'][0]['블로그명']=='과일맘의 기록'
        assert state['rows'][0]['게시글 제목']=='아기첫과일 선택 후기'
        dl=client.get(f'/api/jobs/{jid}/download',headers=hdr)
        assert dl.status_code==200, dl.text
        assert dl.content[:2]==b'PK'
        assert client.get(f'/api/jobs/{jid}/download').status_code == 401


def test_failure_workbook_no_false_success(tmp_path):
    import app as mod
    mod.DATA_DIR = tmp_path
    mod.ACCESS_KEY='test-secret';mod.REQUIRE_KEY=True
    client=TestClient(mod.app);hdr={'X-Access-Key':'test-secret'}
    def fake_fail(config,stop,log):
        log('키워드 접근 제한')
        save_excel(Path(config['dest']),[],[{'keyword':config['keywords'][0],'count':0,'status':'오류','error':'접근 제한'}],config)
        return 0,1,None
    with patch.object(mod,'run_batch',fake_fail):
        res=client.post('/api/jobs',json={'keywords':'실패 테스트'},headers=hdr)
        assert res.status_code==202,res.text
        jid=res.json()['id']
        for _ in range(60):
            state=client.get(f'/api/jobs/{jid}',headers=hdr).json()
            if state['status'] in ('완료','오류','부분 실패','중지'):break
            time.sleep(.1)
        assert state['status']=='오류'
        assert state['downloadReady']
        assert state['count']==0


def test_v12_larger_page_navigation_with_rendered_html(tmp_path):
    """A 30-post page with a real clickable 'next' control yields 95 posts."""
    from unittest.mock import patch
    import threading
    from screen_crawler import crawl_keyword, save_excel

    fixture = '''<!doctype html><html><body>
      <main id="main_pack"><div id="posts"></div>
      <nav class="paginate"><button class="btn_next" id="nextPage">다음 페이지</button></nav></main>
      <script>
      let index = 0; const total = 95; const perPage = 30;
      function draw() {
        const container = document.querySelector('#posts');
        container.innerHTML = '';
        for (let k = index*perPage+1; k <= Math.min(total,(index+1)*perPage);k++){
          const el=document.createElement('article');el.className='result_item';
          el.innerHTML='<a class="title_link" href="https://m.blog.naver.com/fruitmom/'+(1000000000+k)+'">아기 첫과일 후기 '+k+'</a><a href="https://m.blog.naver.com/fruitmom">과일맘</a>';
          container.appendChild(el);
        }
        const more=(index+1)*perPage<total;
        document.getElementById('nextPage').style.display=more?'block':'none';
      }
      document.getElementById('nextPage').addEventListener('click',()=>{index++;draw();window.scrollTo(0,0);});
      draw();
      </script></body></html>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':380,'height':750})
        page=context.new_page()
        htmlfile = tmp_path / 'fixture.html'
        htmlfile.write_text(fixture, encoding='utf-8')
        try:
            with patch('screen_crawler.goto_blog_tab',lambda page,device,kw,log:'테스트 블로그 탭'), \
                 patch('screen_crawler.search_url',lambda keyword,device:htmlfile.as_uri()), \
                 patch('screen_crawler.is_naver_search',lambda url:True), \
                 patch.object(page, 'goto', side_effect=lambda *args,**kwargs:page.set_content(fixture)):
                log=[]
                rows,summary=crawl_keyword(page,'아기첫과일','mobile',100,25,0.01,threading.Event(),log.append,tmp_path)
            assert len(rows)==95, (len(rows),log)
            assert summary['pages']==3,summary
            assert summary['status']=='화면에서 추가 결과 미발견',summary
            assert '다음 페이지' in '\n'.join(log)
            assert [row['화면 수집 순번'] for row in rows]==list(range(1,96))
            assert rows[94]['게시글 제목']=='아기 첫과일 후기 95'
            assert '추가' in summary['reason']
            save_excel(tmp_path/'result.xlsx',rows,[summary],{'device':'mobile','limit':100,'scrolls':25,'pause':1.0})
            from openpyxl import load_workbook
            wb=load_workbook(tmp_path/'result.xlsx',read_only=True)
            headers=[c.value for c in wb['키워드별 현황'][1]]
            assert '종료 사유' in headers and '페이지 이동 횟수' in headers
            wb.close()
        finally:
            context.close();browser.close()


def test_v12_new_upper_bounds():
    from app import JobRequest
    assert JobRequest(keywords='아기첫과일',limit=300,scrolls=80).limit==300
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError): JobRequest(keywords='아기첫과일',limit=301)
    with pytest.raises(ValidationError): JobRequest(keywords='아기첫과일',scrolls=81)
