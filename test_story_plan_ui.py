"""Local browser regression for the Story Plan workspace (requires a running server)."""

import json
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9137"
OUT = Path(tempfile.gettempdir())


def post(request, path, token, data):
    response = request.post(BASE + path, data=data, headers={"Authorization": "Bearer " + token})
    assert response.ok, (path, response.status, response.text())
    return response.json()


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    login = context.request.post(BASE + "/api/login", data={
        "username": "admin", "password": "UiTestPass123!",
    })
    assert login.ok, login.text()
    token = login.json()["token"]
    work = post(context.request, "/api/works", token, {"title": "规划界面验收"})
    chapter1 = post(context.request, f"/api/works/{work['id']}/chapters", token, {"title": "第一章"})
    chapter2 = post(context.request, f"/api/works/{work['id']}/chapters", token, {"title": "第二章"})
    book = post(context.request, f"/api/works/{work['id']}/story-plan", token, {
        "node_type": "book", "title": "故事总纲", "summary": "追寻失踪的档案", "context_policy": "planning_only",
    })
    volume = post(context.request, f"/api/works/{work['id']}/story-plan", token, {
        "node_type": "volume", "parent_id": book["id"], "title": "第一卷：入城",
        "summary": "主角抵达城中", "goal": "找到第一条线索",
    })
    post(context.request, f"/api/works/{work['id']}/story-plan", token, {
        "node_type": "chapter", "parent_id": volume["id"], "chapter_id": chapter1["id"],
        "title": "第一章计划", "goal": "发现陌生来信", "expected_outcome": "取得信件",
    })
    post(context.request, f"/api/works/{work['id']}/story-plan", token, {
        "node_type": "chapter", "parent_id": volume["id"], "chapter_id": chapter2["id"],
        "title": "第二章计划", "goal": "追查来信来源",
    })
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE, wait_until="domcontentloaded")
    page.evaluate("token => localStorage.setItem('token', token)", token)
    page.reload(wait_until="domcontentloaded")
    page.locator("#productionToggleBtn").click()
    page.locator("#productionPlanBtn").click()
    page.locator(".story-plan-route-main").first.wait_for()
    assert page.locator(".story-plan-route-row").count() == 4
    assert not page.locator("#legacyNotesBanner").count()
    before = page.evaluate("currentChapterId")
    page.locator("#productionPlanLocate").select_option(str(chapter2["id"]))
    assert page.evaluate("currentChapterId") == before
    page.locator(".story-plan-route-main").filter(has_text="第一卷：入城").click()
    page.locator("#storyPlanTitle").wait_for()
    assert page.locator("#agentSelection").is_hidden()
    page.locator(".story-plan-form button", has_text="交给 AI").click()
    expect(page.locator("#agentSelection")).to_be_visible(timeout=10000)
    assert page.locator("#storyPlanNewType option").count() == 8
    page.locator(".story-plan-route-main").filter(has_text="第二章计划").click()
    page.locator(".story-plan-advanced summary").click()
    page.get_by_role("button", name="预览写作上下文", exact=True).click()
    expect(page.locator("#storyPlanContextPreview")).to_contain_text("写作上下文已选入")
    page.screenshot(path=str(OUT / "writehtml-plan-1440.png"), full_page=True)
    page.locator("#productionStateBtn").click()
    page.locator("#chapterPlanSummary").get_by_text("本章计划").wait_for()
    page.screenshot(path=str(OUT / "writehtml-state-1440.png"), full_page=True)
    page.set_viewport_size({"width": 1024, "height": 768})
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "writehtml-state-1024.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#aiSide .ai-head-tools button[title='收起']").click()
    page.locator("#productionPlanBtn").click()
    page.locator(".story-plan-route-main").first.wait_for()
    page.locator(".story-plan-route-main").filter(has_text="第一章计划").click()
    page.locator("#storyPlanTitle").wait_for()
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "writehtml-plan-390.png"), full_page=True)
    dimensions = page.evaluate("({ viewport: innerWidth, body: document.body.scrollWidth, inspector: document.querySelector('#productionInspector').getBoundingClientRect().toJSON() })")
    assert dimensions["body"] <= dimensions["viewport"] + 2, dimensions
    assert dimensions["inspector"]["left"] >= -2 and dimensions["inspector"]["right"] <= dimensions["viewport"] + 2, dimensions
    assert not errors, errors
    print(json.dumps({"screenshots": [str(OUT / name) for name in (
        "writehtml-plan-1440.png", "writehtml-state-1440.png", "writehtml-state-1024.png", "writehtml-plan-390.png",
    )], "dimensions": dimensions}, ensure_ascii=False))
    browser.close()
