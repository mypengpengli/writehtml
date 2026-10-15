"""An explicit, machine-readable handoff package; plans never become facts."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

import db
import image_generation
import visual_assets


def _row(row):
    item = dict(row)
    for key in list(item):
        if key.endswith('_json'):
            try:
                item[key[:-5]] = json.loads(item.pop(key))
            except (ValueError, TypeError):
                item[key] = None
    return item


def export_package(wid, uid):
    with db.atomic_transaction() as conn:
        work = db.get_work(wid, uid)
        if not work:
            raise visual_assets.VisualAssetError('作品不存在')
        chapters = db.list_chapters_full(wid, uid) or []
        deleted_chapters = [dict(row) for row in conn.execute('SELECT id,title,ord,deleted_at FROM chapters WHERE work_id=? AND deleted_at IS NOT NULL', (wid,))]
        chapter_status = {chapter['id']: 'active' for chapter in chapters}
        chapter_status.update({chapter['id']: 'deleted' for chapter in deleted_chapters})
        characters = [_row(row) for row in conn.execute("SELECT id,work_id,name,kind,summary,detail FROM entities WHERE work_id=? AND kind='人物' ORDER BY id", (wid,))]
        cards = [_row(row) for row in conn.execute("SELECT * FROM production_cards WHERE work_id=? ORDER BY id", (wid,))]
        plans = db.list_story_plan_nodes(wid, uid, include_abandoned=True) or []
        scenes = [_row(row) for row in conn.execute("SELECT * FROM production_scenes WHERE chapter_id IN (SELECT id FROM chapters WHERE work_id=? AND deleted_at IS NULL) AND stale=0 ORDER BY chapter_id,ord,id", (wid,))]
        state_versions = {
            'characters': [_row(row) for row in conn.execute("SELECT * FROM entity_state_versions WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?) ORDER BY id", (wid,))],
            'cards': [_row(row) for row in conn.execute("SELECT * FROM production_card_versions WHERE card_id IN (SELECT id FROM production_cards WHERE work_id=?) ORDER BY id", (wid,))],
            'plot': [_row(row) for row in conn.execute("SELECT * FROM plot_state_versions WHERE work_id=? ORDER BY id", (wid,))],
        }
        images = visual_assets.list_images(wid, uid)
        paths = {row['id']: row['image_path'] for row in conn.execute("SELECT id,image_path FROM resource_images WHERE work_id=? AND user_id=?", (wid, uid))}
        documents = [_row(row) for row in conn.execute("SELECT * FROM document_imports WHERE work_id=? AND user_id=? ORDER BY id", (wid, uid))]
    package = {
        'format': 'writehtml-creative-package', 'version': 1, 'work': work,
        'semantics': {'characters_and_cards': 'canonical_base_settings', 'story_plans': 'author_plans_not_happened_facts',
                      'chapters': 'written_prose', 'state_versions': 'chapter_scoped_versions_check_stale_and_evidence',
                      'images': 'visual_references_not_confirmed_story_facts', 'unbound_images': 'unclassified_do_not_infer_owner'},
        'chapter_order': [{'ref': f"chapter:{chapter['id']}", 'id': chapter['id'], 'ord': chapter['ord'], 'title': chapter['title']} for chapter in chapters],
        'deleted_chapters': [{**chapter, 'ref': f"chapter:{chapter['id']}", 'status': 'deleted'} for chapter in deleted_chapters],
        'characters': characters, 'cards': cards, 'story_plans': plans, 'scenes': scenes,
        'state_versions': state_versions, 'images': [], 'documents': [],
    }
    handle, path = tempfile.mkstemp(prefix='writehtml-export-', suffix='.zip')
    os.close(handle)
    try:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for chapter in chapters:
                archive.writestr(f"prose/chapter-{chapter['id']}.md", chapter.get('content') or '')
            for document in documents:
                archive.writestr(f"sources/document-{document['id']}.txt", document.pop('text'))
                package['documents'].append({**document, 'ref': f"document:{document['id']}", 'file': f"sources/document-{document['id']}.txt"})
            for image in images:
                original = image_generation.resolve_image_path(paths[image['id']])
                if not original.is_file():
                    raise visual_assets.VisualAssetError(f"图片 #{image['id']} 的文件不存在，请修复后再导出；未输出不完整素材包")
                group = image['category'] if image['category'] != 'unbound' else 'unclassified'
                owner = f"{image['target_type']}-{image['target_id']}" if image['target_type'] else 'unclassified'
                relative = f"images/{group}/{owner}/image-{image['id']}{original.suffix.lower()}"
                data = original.read_bytes()
                archive.writestr(relative, data)
                package['images'].append({**image, 'ref': f"image:{image['id']}", 'file': relative,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'target_ref': f"{image['target_type']}:{image['target_id']}" if image['target_type'] else None,
                    'parent_image_ref': f"image:{image['parent_image_id']}" if image['parent_image_id'] else None,
                    'chapter_ref': f"chapter:{image['chapter_id']}" if image['chapter_id'] else None,
                    'chapter_status': chapter_status.get(image['chapter_id'], 'missing') if image['chapter_id'] else 'unspecified',
                    'document_ref': f"document:{image['document_id']}" if image['document_id'] else None})
            archive.writestr('manifest.json', json.dumps(package, ensure_ascii=False, indent=2))
            archive.writestr('README.txt',
                'WriteHTML 创作素材包\n\n以 manifest.json 为唯一关联索引，不要根据文件名或图片相似度重新猜测归属。\n'
                'character:<id> 是人物，card:<id> 是设定；image:<id> 的 target_ref 明确指向所属对象。\n'
                'variant_kind/parent_image_ref 标明衍生类型及来源；chapter_ref 只表示作者标注的关联时点。\n'
                'selected 表示作者选定的主图，不表示此图适用于所有历史章节。\n'
                '未绑定图片位于 images/unclassified，不得自动指定角色。\n'
                'story_plans 是作者未来计划，不是已发生剧情；prose 是已写正文。\n'
                'state_versions 为按章节变化的记录，使用时需核对 stale、来源和 chapter_order。\n'
                'cards 中 status=archived 的卡片、deleted_chapters 和图片 chapter_status=deleted/missing 只作来源说明，不得当作当前有效剧情。\n'
                'sources 是原文资料，不等于正式世界设定。图片为视觉参考，不会自动产生剧情事实。\n')
        return path
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
