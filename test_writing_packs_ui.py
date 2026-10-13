"""Local UI regression for writing Skills, long references and diagnostic handoff."""
import json
import os
import re
from pathlib import Path
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright


base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9138"
screenshots = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL', 'chrome') or None, headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    response = context.request.post(base + "/api/login", data={"username": "admin", "password": "UiTestPass123!"})
    assert response.ok, response.text()
    token = response.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    work = context.request.post(base + "/api/works", data={"title": "写作工具验收"}, headers=headers).json()
    chapter = context.request.post(base + f"/api/works/{work['id']}/chapters", data={"title": "车站"}, headers=headers).json()
    page = context.new_page()
    errors, ai_requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda req: ai_requests.append(req.url) if "/api/agent/stream" in req.url else None)
    page.goto(base, wait_until="domcontentloaded")
    page.evaluate("token => localStorage.setItem('token', token)", token)
    page.reload(wait_until="networkidle")
    page.evaluate("wid => selectWork(wid)", work["id"])
    page.wait_for_load_state("networkidle")
    page.evaluate("openSkillPicker()")
    expect(page.locator("#skillPickerList")).to_contain_text("人物设计")
    expect(page.locator("#skillPickerList .skill-pick-row")).to_have_count(12)
    page.locator("#skillPickerSearch").fill("人物卡")
    expect(page.locator("#skillPickerList .skill-pick-choice")).to_have_count(1)
    page.locator("#skillPickerSearch").fill("修订与去模板感")
    row = page.locator("#skillPickerList .skill-pick-row")
    row.locator("input").check()
    row.get_by_title("查看技能资料").click()
    expect(page.locator("#skillResourcePath option")).to_have_count(11)
    page.locator("#skillResourcePath").select_option("references/narrative-craft.md")
    expect(page.locator("#skillResourceNext")).to_be_enabled()
    first_page = page.locator("#skillResourceText").inner_text()
    page.locator("#skillResourceNext").click()
    expect(page.locator("#skillResourcePosition")).to_contain_text("6000 /")
    assert page.locator("#skillResourceText").inner_text() != first_page
    page.locator("#skillResourceQuery").fill("C类")
    page.locator("#skillResourceQuery").press("Enter")
    expect(page.locator("#skillResourceText")).to_contain_text("C类")
    search_offset = page.evaluate("skillResourceOffset")
    assert search_offset > 6000
    page.locator("#skillResourcePrev").click()
    expect(page.locator("#skillResourcePosition")).to_contain_text(f"{max(0, search_offset - 6000)} /")
    for width in (1440, 1024, 390):
        page.set_viewport_size({"width": width, "height": 900 if width > 700 else 844})
        bounds = page.locator(".skill-resource-dialog").bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
        if width >= 1024:
            assert bounds["width"] >= 800
        assert page.evaluate("document.body.scrollWidth <= innerWidth")
        assert page.locator("#skillResourceText").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        path = Path(tempfile.gettempdir()) / f"writehtml-skill-resources-{width}.png"
        page.screenshot(path=str(path))
        screenshots.append(str(path))
    page.keyboard.press("Escape")
    expect(page.locator("#skillResourceOverlay")).to_be_hidden()
    page.locator("#skillPickerSearch").fill("")
    path = Path(tempfile.gettempdir()) / "writehtml-skill-picker-390.png"
    page.screenshot(path=str(path))
    screenshots.append(str(path))
    page.evaluate("clearActiveSkills()")
    choices = page.locator("#skillPickerList input[type=checkbox]")
    for index in range(5):
        choices.nth(index).click()
    expect(page.locator("#skillPickerList input:checked")).to_have_count(4)
    page.evaluate("closeSkillPicker()")
    text = "他很紧张。\n他推开车站那扇生锈的铁门。\n他推开车站那扇生锈的铁门。\n【自检报告】内容完整。"
    page.locator("#content").fill(text)
    page.locator('.topbar button[title="更多"]').click()
    page.locator("#writingScanBtn").click()
    expect(page.locator("#writingQualityBody")).to_contain_text("待复核候选")
    expect(page.locator("#writingQualityBody")).to_contain_text("交付杂质候选")
    stored = context.request.get(base + f"/api/chapters/{chapter['id']}", headers=headers).json()
    assert stored["content"] == text
    suggestion = page.locator(".writing-scan-list section p").first
    assert suggestion.evaluate("el => getComputedStyle(el).display") == "block"
    assert suggestion.bounding_box()["width"] >= 230
    paging = page.locator(".writing-scan-paging button")
    assert abs(paging.nth(0).bounding_box()["y"] - paging.nth(1).bounding_box()["y"]) < 1
    page.locator("#writingQualityBody").evaluate("el => el.scrollTop = el.scrollHeight")
    last = page.locator(".writing-scan-list section p").last.bounding_box()
    footer = page.locator("#writingQualityActions").bounding_box()
    assert last["y"] + last["height"] <= footer["y"]
    page.locator("#writingQualityBody").evaluate("el => el.scrollTop = 0")
    path = Path(tempfile.gettempdir()) / "writehtml-writing-scan-390.png"
    page.screenshot(path=str(path))
    screenshots.append(str(path))
    page.get_by_role("button", name="交给 AI 复核", exact=True).click()
    expect(page.locator("#agentInput")).to_have_value(re.compile(".*暂不修改正文.*", re.S))
    assert not ai_requests, "Handoff must not send before the author is ready"
    page.evaluate("clearActiveSkills()")
    page.locator("#content").evaluate("el => el.setSelectionRange(0, 6)")
    page.evaluate("runWritingScan(true)")
    expect(page.locator("#writingQualityTitle")).to_have_text("选区文字检查")
    expect(page.locator("#writingQualityBody")).to_contain_text("情绪定性")
    assert "交付杂质候选" not in page.locator("#writingQualityBody").inner_text()
    assert not errors, errors
    browser.close()
print(json.dumps({"screenshots": screenshots, "errors": errors}, ensure_ascii=False))
