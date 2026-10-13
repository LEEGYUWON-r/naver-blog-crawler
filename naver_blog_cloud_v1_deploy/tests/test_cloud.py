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
