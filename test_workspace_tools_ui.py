"""Additional workspace checks, run against test_workspace_ui's isolated server."""
from pathlib import Path
import subprocess
import sys

from playwright.sync_api import expect


def run_checks(page, output):
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.reload()
    expect(page.locator('#content')).to_be_visible()
    page.wait_for_function("chaptersLoadedWorkId === currentWorkId && chapters.length >= 2 && $('chapTitle').value")
    work_id = page.evaluate('currentWorkId')
    original = page.evaluate('chapters.map(c=>c.id)')
    page.evaluate('moveChapter(chapters[0].id, 1)')
    server_ids = page.evaluate("async () => (await api(`/api/works/${currentWorkId}/chapters`,{method:'GET'})).map(c=>c.id)")
    assert server_ids == [original[1], original[0], *original[2:]]
    assert page.evaluate('chapters.map(c=>c.ord)') == list(range(1, len(original) + 1))

    route_path = f'**/api/works/{work_id}/reorder'
    page.route(route_path, lambda route: route.fulfill(status=503, body='test offline'))
    page.evaluate('moveChapter(chapters[0].id, 1)')
    assert page.evaluate('chapters.map(c=>c.id)') == server_ids
    expect(page.locator('#toast')).to_contain_text('排序未确认')
    page.unroute(route_path)

    blocked = []
    page.route(route_path, lambda route: blocked.append(route))
    page.evaluate('() => { window.pendingChapterOrder = moveChapter(chapters[0].id,1); }')
    page.wait_for_function('chapterOrderBusy.has(currentWorkId)')
    page.evaluate('moveChapter(chapters[0].id,1)')
    assert len(blocked) == 1, 'Overlapping reorder requests were sent'
    page.evaluate("""async () => {
      const w=await api('/api/works',{body:{title:'排序隔离作品'}});
      await api(`/api/works/${w.id}/chapters`,{body:{title:'另一作品章节'}});
      await selectWork(w.id);
    }""")
    blocked[0].fulfill(status=503, body='delayed failure')
    page.evaluate('pendingChapterOrder')
    expect(page.locator('#chapTitle')).to_have_value('另一作品章节')
    assert page.evaluate('chapters.length') == 1
    page.unroute(route_path)
    page.evaluate('wid => selectWork(wid)', work_id)

    page.set_viewport_size({'width':390,'height':844})
    page.evaluate("() => { if($('app').classList.contains('ai-open')) toggleAISide(); }")
    page.evaluate("$('app').classList.add('side-open')")
    page.locator('.c-more').first.click()
    menu = page.locator('#chapterMenu')
    expect(menu).to_be_visible()
    box = menu.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390
    assert page.locator('.c-more').first.bounding_box()['height'] >= 44
    page.screenshot(path=str(output / 'chapter-menu-mobile.png'))
    page.keyboard.press('Escape')
    expect(menu).to_have_count(0)
    expect(page.locator('.c-more').first).to_be_focused()
    page.evaluate("$('app').classList.remove('side-open')")
    page.set_viewport_size({'width':1440,'height':900})

    # Use true editor character counts (whitespace differs from server list metadata).
    page.evaluate(r"""async () => {
      await flushEditorSave();
      $('content').value='起点\n\n'; onContentInput(); await flushEditorSave();
      localStorage.removeItem(`writehtml:daily:${currentUsername}:${currentWorkId}`); updateWC();
    }""")
    expect(page.locator('#wc')).to_contain_text('本机今日净增 +0')
    page.locator('#content').fill('起点\n\n' + '字' * 100)
    expect(page.locator('#wc')).to_contain_text('本机今日净增 +100')
    page.evaluate('flushEditorSave()')
    page.reload()
    expect(page.locator('#wc')).to_contain_text('本机今日净增 +100')
    storage_refresh = page.evaluate("""() => {
      const key=`writehtml:daily:${currentUsername}:${currentWorkId}`, original=localStorage.getItem(key), record=JSON.parse(original);
      record.deltas[currentChapterId]+=25; const incoming=JSON.stringify(record); localStorage.setItem(key,incoming);
      dispatchEvent(new StorageEvent('storage',{key,newValue:incoming}));
      const result={unchanged:localStorage.getItem(key)===incoming, text:$('wc').textContent};
      localStorage.setItem(key,original); updateWC(); return result;
    }""")
    assert storage_refresh['unchanged'] and '本机今日净增 +125' in storage_refresh['text'], storage_refresh
    imported = page.evaluate("""async () => {
      const c=await api(`/api/works/${currentWorkId}/chapters`,{body:{title:'导入进度测试'}});
      await api(`/api/chapters/${c.id}`,{method:'PUT',body:{content:'外部内容'.repeat(100)}});
      await loadChapters(); await selectChapter(c.id); return c.id;
    }""")
    expect(page.locator('#wc')).to_contain_text('本机今日净增 +100')
    page.evaluate("""async id => {
      await api(`/api/chapters/${id}`,{method:'DELETE'}); await loadChapters();
    }""", imported)
    expect(page.locator('#wc')).to_contain_text('本机今日净增 +100')

    page.keyboard.press('ControlOrMeta+k')
    expect(page.locator('#commandOverlay')).to_be_visible()
    page.locator('#commandInput').fill('导出')
    assert page.locator('#commandList [role="option"]').count() >= 4
    page.locator('#commandInput').dispatch_event('keydown', {'key':'Enter','isComposing':True})
    expect(page.locator('#commandOverlay')).to_be_visible()
    page.locator('#commandInput').fill('撤销隔离测试')
    page.locator('#commandInput').press('Enter')
    expect(page.locator('#commandOverlay')).to_be_hidden()
    expect(page.locator('#chapTitle')).to_have_value('撤销隔离测试')

    relation_id = page.evaluate("""async () => {
      const people=[];
      for(let i=0;i<12;i++) people.push(await api(`/api/works/${currentWorkId}/entities`,{body:{name:'人物'+i,kind:'人物',summary:'测试',detail:''}}));
      let first;
      for(let i=1;i<people.length;i++) {
        const r=await api(`/api/works/${currentWorkId}/relationships`,{body:{from_entity_id:people[0].id,to_entity_id:people[i].id,relation:'同伴'+i,status:i===3?'ended':'active'}});
        first ||= r;
      }
      await api(`/api/works/${currentWorkId}/relationships`,{body:{from_entity_id:people[1].id,to_entity_id:people[0].id,relation:'师徒',status:'active'}});
      openStoryDrawer('relations'); await loadRelationships(); return first.id;
    }""")
    expect(page.locator('#relationMap svg')).to_be_visible()
    assert page.locator('#relationMap .rg-node').count() == 12
    assert page.locator('#relationList .relation-row').count() == 12
    graph = page.locator('#relationMap')
    for theme in ('light','dark'):
        page.evaluate('t => applyTheme(t)', theme)
        contrast = page.evaluate("""() => {
          const styles=getComputedStyle(document.documentElement), c=k=>styles.getPropertyValue(k).trim();
          const lum=h=>{const v=[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return v[0]*.2126+v[1]*.7152+v[2]*.0722;};
          const ratio=(a,b)=>{a=lum(c(a));b=lum(c(b));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
          return {muted:ratio('--muted','--side-bg'),warning:ratio('--warn-ink','--side-bg'),node:ratio('--node-before','--panel')};
        }""")
        assert min(contrast.values()) >= 4.5, (theme, contrast)
        page.screenshot(path=str(output / f'relations-{theme}.png'))
    page.get_by_title('放大人物关系图').click()
    expect(page.locator('#relationGraphOverlay')).to_be_visible()
    page.locator(f'#relationGraphExpanded [data-relation="{relation_id}"]').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#relationGraphOverlay')).to_be_hidden()
    expect(page.locator('#relationFormTitle')).to_have_text('编辑关系')
    expect(page.locator('#relationName')).to_be_focused()
    # Malicious labels remain text; large graphs are bounded rather than hanging the editor.
    result = page.evaluate("""() => {
      const many=Array.from({length:200},(_,i)=>({id:5000+i,from_entity_id:i+1,to_entity_id:i+2,from_name:'<img src=x onerror=alert(1)>',to_name:'长人物名'+i,relation:'<script>bad()</script>',status:'active'}));
      const start=performance.now(); renderRelationGraph($('relationMap'),many);
      return {ms:performance.now()-start,nodes:$('relationMap').querySelectorAll('.rg-node').length,unsafe:$('relationMap').querySelectorAll('img,script').length};
    }""")
    assert result['nodes'] <= 28 and result['unsafe'] == 0
    assert result['ms'] < 1500, result
    page.evaluate('renderRelations(); closeStoryDrawer()')

    page.evaluate('openProductionCanvas()')
    page.locator('#productionStateBtn').click()
    expect(page.locator('#productionCanvasViewport')).to_be_visible()
    assert page.locator('.production-node.before .production-node-row').count() >= 12
    expect(page.locator('.production-zoombar')).to_be_visible()
    assert page.locator('#productionCanvas .production-canvas-toolbar').count() == 0
    for width in (1440,1100):
        page.set_viewport_size({'width':width,'height':900})
        page.evaluate("""() => { $('productionWorkspace').classList.add('resources-closed'); fitProductionCanvas(); }""")
        assert page.evaluate("""() => {
          const r=$('productionCanvasViewport').getBoundingClientRect();
          return [...$('productionCanvas').querySelectorAll('.production-node')].every(n=>{const b=n.getBoundingClientRect();return b.left>=r.left-1 && b.right<=r.right+1 && b.bottom<=r.bottom+1;});
        }"""), f'Fit did not contain all chapter nodes at {width}'
        page.screenshot(path=str(output / f'chapter-canvas-{width}.png'))
    page.evaluate("setProductionZoomAt(1); $('productionCanvasViewport').scrollTo(180,80)")
    drift = page.evaluate("""() => {
      const vp=$('productionCanvasViewport'), c=$('productionCanvas'), r=vp.getBoundingClientRect(), b=c.getBoundingClientRect();
      const x=r.left+160,y=r.top+170, px=(x-b.left)/productionZoom, py=(y-b.top)/productionZoom;
      setProductionZoomAt(1.2,x,y); const after=c.getBoundingClientRect();
      return Math.max(Math.abs(after.left+px*productionZoom-x),Math.abs(after.top+py*productionZoom-y));
    }""")
    assert drift < 2, drift
    page.keyboard.press('ControlOrMeta+k')
    expect(page.locator('#commandOverlay')).to_be_visible()
    page.keyboard.press('Escape')
    assert page.evaluate("$('app').classList.contains('production-open')")
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('.production-zoombar')).to_be_hidden()
    page.wait_for_function("parseFloat($('productionCanvas').style.zoom) === 1")
    assert page.evaluate('document.body.scrollWidth <= innerWidth')
    page.set_viewport_size({'width':1100,'height':900})
    page.wait_for_function("Math.abs(parseFloat($('productionCanvas').style.zoom) - productionZoom) < .001")
    expect(page.locator('.production-zoombar')).to_be_visible()
    print('Workspace tools passed: reorder/failure/scope, daily progress, commands, bounded graph and canvas zoom')


if __name__ == '__main__':
    subprocess.run([sys.executable, str(Path(__file__).with_name('test_workspace_ui.py'))], check=True)
