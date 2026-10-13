"""Isolated browser regression for layout, inspector saves and navigation."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

from playwright.sync_api import expect, sync_playwright


root = Path(__file__).resolve().parent
output = root / '.smoke_tmp' / ('workspace-ui-' + uuid.uuid4().hex[:8])
output.mkdir(parents=True)
os.environ.update(DB_PATH=str(output / 'test.db'), WRITEHTML_ADMIN_PASSWORD='UiTestPass123!',
                  INSPIRATION_WORKER_ENABLED='false', PYTHONUTF8='1')
import db

db.init_db()
uid = db.verify_user('admin', 'UiTestPass123!')['id']
work = db.create_work(uid, '雾港 UI 验收')
chapter = db.create_chapter(work['id'], uid, '雨夜来信')
db.update_chapter(chapter['id'], uid, None, '林晚抵达港口。\n' * 100, None)
db.create_chapter(work['id'], uid, '灯塔证人')
entity = db.create_entity(work['id'], uid, '林晚', '人物', '档案修复师', '谨慎，善于观察')
card = db.save_production_card(work['id'], uid, {'category': 'rule', 'name': '潮汐规则',
    'summary': '大潮会送来旧信', 'detail': '信中只有已经发生的事'})
book = db.create_story_plan_node(work['id'], uid, {'node_type': 'book', 'title': '雾港往事'})
db.create_story_plan_node(work['id'], uid, {'node_type': 'chapter', 'title': '追查来信',
    'chapter_id': chapter['id'], 'parent_id': book['id'], 'goal': '找出寄信人'})
db.save_conversation(uid, chapter['id'], [
    {'role': 'user', 'content': '查一下当前资料'},
    {'role': 'tool', 'name': 'read_chapter', 'content': json.dumps({'summary': '已读正文'})},
    {'role': 'tool', 'name': 'list_characters', 'content': json.dumps({'summary': '已读人物'})},
    {'role': 'tool', 'name': 'save_character_card', 'content': json.dumps({'summary': '已保存人物'})},
    {'role': 'tool', 'name': 'read_chapter', 'content': json.dumps({'error': '测试错误应保持可见'})},
    {'role': 'assistant', 'content': '资料已读取。'},
], '')

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
base = f'http://127.0.0.1:{port}'
log = (output / 'server.log').open('w', encoding='utf-8')
server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                           '--port', str(port), '--log-level', 'warning'], cwd=root, stdout=log, stderr=log)
try:
    for _ in range(150):
        try:
            urllib.request.urlopen(base, timeout=.5).close()
            break
        except OSError:
            if server.poll() is not None:
                raise RuntimeError((output / 'server.log').read_text(encoding='utf-8'))
            time.sleep(.1)
    else:
        raise RuntimeError('Local UI server did not start')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        page.get_by_placeholder('用户名', exact=True).fill('admin')
        page.get_by_placeholder('密码', exact=True).fill('UiTestPass123!')
        page.get_by_role('button', name='登录', exact=True).click()
        expect(page.locator('#chapterSearch')).to_be_visible()
        page.locator('#chapterSearch').fill('1')
        page.locator('#chapterSearch').press('Enter')
        expect(page.locator('#chapTitle')).to_have_value('雨夜来信')
        page.locator('#chapterSearch').fill('')
        page.locator('#aiToggleBtn').click()
        expect(page.locator('.agent-tool-group')).to_have_count(1)
        expect(page.locator('.tool-danger')).to_be_visible()
        page.locator('.agent-tool-group summary').click()
        expect(page.locator('.agent-tool-group .cm.action')).to_have_count(3)
        for width in (1920, 1440, 1024):
            page.set_viewport_size({'width': width, 'height': 900})
            page.wait_for_function("document.querySelector('.main').getBoundingClientRect().right <= document.querySelector('#aiSide').getBoundingClientRect().left + 1 && document.querySelector('.main').getBoundingClientRect().width >= 540")
            bounds = page.locator('.main').bounding_box()
            assert bounds['width'] >= 540
            assert page.evaluate('document.body.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / f'editor-{width}.png'))
        page.locator('#storyToggleBtn').click()
        page.wait_for_function("document.querySelector('#storyDrawer').getBoundingClientRect().left < 2")
        assert page.locator('#storyDrawer').bounding_box()['y'] >= 50
        assert page.locator('#aiSide').is_visible()
        page.locator('#storyDrawer button[title="关闭"]').click()
        page.locator('#content').evaluate('el => { el.setSelectionRange(25, 35); el.scrollTop = 250; }')
        before = page.locator('#content').evaluate('el => ({start:el.selectionStart,end:el.selectionEnd,scroll:el.scrollTop})')
        page.locator('#productionToggleBtn').click()
        page.locator('#productionCanonBtn').click()
        expect(page.locator('.production-world-groups section')).to_have_count(2)
        page.locator('.production-world-groups button').filter(has_text='潮汐规则').click()
        expect(page.locator('#productionCardName')).to_be_visible()
        expect(page.locator('#productionCardScope')).to_be_hidden()
        page.locator('#productionCardName').fill('潮汐规则修订')
        page.get_by_role('tab', name='本章状态', exact=True).click()
        page.locator('#productionCardState').fill('状态：已被主角发现')
        expect(page.locator('[data-save-kind="state"]')).to_contain_text('待记录')
        expect(page.locator('[data-save-kind="base"]')).to_contain_text('已同步', timeout=10000)
        assert db.get_production_card(card['id'], uid)['name'] == '潮汐规则修订'
        assert not db.get_production_card(card['id'], uid)['versions']
        page.get_by_role('button', name='记录本章状态', exact=True).click()
        expect(page.locator('[data-save-kind="state"]')).to_contain_text('已记录', timeout=10000)
        page.get_by_role('tab', name='历史版本', exact=True).click()
        expect(page.locator('[data-inspector-panel="history"]')).to_contain_text('已被主角发现')
        page.locator('#productionInspector button[title="关闭详情"]').click()
        page.locator('.production-world-groups button').filter(has_text='潮汐规则修订').click()
        expect(page.locator('[data-save-kind="state"]')).to_contain_text('已记录')
        assert '待记录' not in page.locator('[data-save-kind="state"]').inner_text()
        page.locator('#productionInspector button[title="关闭详情"]').click()
        page.locator('#productionPlanBtn').click()
        page.locator('.story-plan-route-main').filter(has_text='追查来信').click()
        expect(page.locator('#productionBreadcrumb')).to_contain_text('雾港往事')
        expect(page.locator('#productionInspectorFooter')).to_be_visible()
        for width in (1440, 1024, 390):
            page.set_viewport_size({'width': width, 'height': 844})
            if width == 390:
                page.locator('#aiSide button[title="收起"]').click()
            page.wait_for_function("document.querySelector('#productionInspector').getBoundingClientRect().right <= innerWidth + 1")
            assert page.evaluate('document.body.scrollWidth <= innerWidth')
            footer = page.locator('#productionInspectorFooter').bounding_box()
            assert footer['y'] + footer['height'] <= 845
            if width == 390:
                assert page.locator('.production-head').bounding_box()['height'] <= 115
            page.screenshot(path=str(output / f'inspector-{width}.png'))
        page.locator('#productionInspector button[title="关闭详情"]').click()
        page.locator('button[title="返回正文编辑器"]').click()
        after = page.locator('#content').evaluate('el => ({start:el.selectionStart,end:el.selectionEnd,scroll:el.scrollTop})')
        assert after['start'] == before['start'] and after['end'] == before['end']
        page.locator('#aiToggleBtn').click()
        expect(page.locator('#writingGuideCard.compact')).to_be_visible()
        composer = page.locator('#agentInput').bounding_box()
        assert composer['width'] >= 350
        assert page.locator('#agentMicBtn').bounding_box()['y'] >= composer['y'] + composer['height']
        assert not errors, errors
        browser.close()
    print('Workspace UI checks passed. Screenshots:', output)
    for regression in ('test_story_plan_ui.py', 'test_writing_packs_ui.py'):
        subprocess.run([sys.executable, regression, base], cwd=root,
                       env={**os.environ, 'BROWSER_CHANNEL': ''}, check=True)
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
