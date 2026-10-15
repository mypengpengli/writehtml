"""Read Word text and image occurrences in document order, without remote fetches."""
import hashlib
import io
import json
import re
import time
import zipfile

from docx import Document
from docx.oxml.ns import qn

import db
import image_generation
import visual_assets


def parse_word(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise visual_assets.VisualAssetError("Word 文档解压后过大")
    document = Document(io.BytesIO(data))
    paragraphs = list(document.element.body.iter(qn('w:p')))
    texts = [''.join(node.text or '' for node in p.iter(qn('w:t'))).strip() for p in paragraphs]
    blocks, images, warnings, consumed = [], [], [], set()
    heading = ''
    for position, paragraph in enumerate(paragraphs):
        text = texts[position]
        style = paragraph.find('./' + qn('w:pPr') + '/' + qn('w:pStyle'))
        style_id = style.get(qn('w:val')) if style is not None else ''
        if text and (re.search(r'heading|标题', style_id, re.I) or re.match(r'^第.{1,12}[章节卷篇部]', text)):
            heading = text[:240]
        if text:
            blocks.append(text)
        refs = []
        for node in paragraph.iter():
            if node.tag == qn('a:blip') and node.get(qn('r:embed')):
                ref = node.get(qn('r:embed'))
            elif node.tag == '{urn:schemas-microsoft-com:vml}imagedata' and node.get(qn('r:id')):
                ref = node.get(qn('r:id'))
            else:
                continue
            container = node
            caption = ''
            while container is not None and container is not paragraph:
                props = list(container.iter(qn('wp:docPr')))
                if props:
                    caption = props[0].get('descr') or props[0].get('title') or ''
                    break
                container = container.getparent()
            refs.append((ref, ' '.join(caption.split())[:200]))
        for ref, caption in refs:
            index = len(images) + 1
            if index > 200:
                raise visual_assets.VisualAssetError("Word 中图片超过 200 张，请拆分文档后导入")
            relationship = document.part.rels.get(ref)
            if relationship is None or relationship.is_external:
                warnings.append(f"第 {position + 1} 段图片引用不可读取；未访问外部地址")
                continue
            part = relationship.target_part
            consumed.add(str(part.partname).lstrip('/'))
            before = '\n'.join(texts[max(0, position - 2):position + 1])[-700:]
            after = '\n'.join(texts[position + 1:position + 3])[:700]
            context = f"标题：{heading}\n图片说明：{caption}\n图片前文：{before}\n图片后文：{after}"
            try:
                visual_assets.image_info(part.blob)
            except visual_assets.VisualAssetError as exc:
                message = f"{part.partname}：{exc}"
                warnings.append(message)
                blocks.append(f"[未导入的图片：{caption or part.partname}；{exc}]")
                continue
            images.append({'data': part.blob, 'source_name': str(part.partname).rsplit('/', 1)[-1],
                           'source_index': index, 'source_context': context, 'label': caption or f'文档图片 {index}'})
            blocks.append(f'[[WORD_IMAGE_{index}]]')
    # Media outside body paragraphs (for example a header logo) remains available, but unclassified.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not name.startswith('word/media/') or name.endswith('/') or name in consumed:
                continue
            blob = archive.read(name)
            try:
                visual_assets.image_info(blob)
            except visual_assets.VisualAssetError as exc:
                warnings.append(f'{name}：{exc}')
                continue
            index = len(images) + 1
            if index > 200:
                raise visual_assets.VisualAssetError('Word 中图片超过 200 张，请拆分文档后导入')
            label = f'未定位文档图片 {index}'
            images.append({'data': blob, 'source_name': name.rsplit('/', 1)[-1], 'source_index': index,
                           'source_context': '未定位到正文段落，可能来自页眉、页脚等区域；不可猜测归属。', 'label': label})
            blocks.append(f'[[WORD_IMAGE_{index}]]')
    return {'text': '\n'.join(blocks), 'images': images, 'warnings': warnings}


def import_word(filename, data, uid, wid):
    source_hash = hashlib.sha256(data).hexdigest()
    with db.get_conn() as conn:
        if not db._work_owned(conn, wid, uid):
            raise visual_assets.VisualAssetError('请先选择当前作品再导入 Word')
        existing = conn.execute('SELECT id FROM document_imports WHERE user_id=? AND work_id=? AND source_hash=?', (uid, wid, source_hash)).fetchone()
    if existing:
        return visual_assets.get_document(existing['id'], uid, wid)
    parsed = parse_word(data)
    if not parsed['text'].strip() and not parsed['images']:
        raise visual_assets.VisualAssetError('Word 中没有可读取的文字或图片')
    paths, staged, committed = [], [], False
    try:
        # Decode and write files outside the SQLite writer lock; metadata commits as one batch.
        for source in parsed['images']:
            path, _ = image_generation.save_image(uid, 0, source['data'])
            paths.append(path)
            width, height = visual_assets.image_info(source['data'])
            staged.append((source, path, width, height))
        with db.atomic_transaction(immediate=True) as conn:
            if not db._work_owned(conn, wid, uid):
                raise visual_assets.VisualAssetError("请先选择当前作品再导入 Word")
            existing = conn.execute("SELECT id FROM document_imports WHERE user_id=? AND work_id=? AND source_hash=?", (uid, wid, source_hash)).fetchone()
            if existing:
                return visual_assets.get_document(existing['id'], uid, wid)
            cur = conn.execute("INSERT INTO document_imports(user_id,work_id,name,source_hash,text,warnings_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (uid, wid, filename, source_hash, '', json.dumps(parsed['warnings'], ensure_ascii=False), time.time()))
            document_id = cur.lastrowid
            text = parsed['text']
            now = time.time()
            for source, path, width, height in staged:
                image = conn.execute('INSERT INTO resource_images(user_id,work_id,image_path,label,source_name,source_context,document_id,source_index,content_hash,width,height,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (uid,wid,path,source['label'][:200],source['source_name'],source['source_context'],document_id,source['source_index'],hashlib.sha256(source['data']).hexdigest(),width,height,now,now))
                text = text.replace(f"[[WORD_IMAGE_{source['source_index']}]]", f"[文档图片 #{image.lastrowid}：{source['label']}]")
            conn.execute("UPDATE document_imports SET text=? WHERE id=?", (text, document_id))
        committed = True
        return visual_assets.get_document(document_id, uid, wid)
    finally:
        if not committed:
            for path in paths:
                image_generation.remove_image(path)


def prose_sections(document):
    text = document['text']
    pattern = re.compile(r'^\s*(第[零〇一二三四五六七八九十百千万两\d]{1,12}[章节回][^\n]{0,180})\s*$', re.M)
    matches = list(pattern.finditer(text))
    return [{'index': index + 1, 'title': match.group(1).strip(), 'start': match.start(),
             'end': matches[index + 1].start() if index + 1 < len(matches) else len(text)}
            for index, match in enumerate(matches)]


def import_chapter(document_id, uid, wid, index):
    with db.atomic_transaction(immediate=True) as conn:
        document = visual_assets.get_document(document_id, uid, wid)
        if not document:
            raise visual_assets.VisualAssetError("文档不存在或不属于当前作品")
        sections = prose_sections(document)
        section = next((item for item in sections if item['index'] == index), None)
        if not section:
            raise visual_assets.VisualAssetError("章节段落不存在；先读取文档章节索引")
        existing = conn.execute("SELECT chapter_id FROM document_chapter_imports WHERE document_id=? AND section_index=?", (document_id, index)).fetchone()
        if existing and conn.execute('SELECT 1 FROM chapters WHERE id=? AND deleted_at IS NOT NULL', (existing['chapter_id'],)).fetchone():
            raise visual_assets.VisualAssetError('该章节此前已导入，当前位于回收站；请先恢复或彻底删除后再导入')
        if existing and db.get_chapter(existing['chapter_id'], uid):
            return {"chapter_id": existing['chapter_id'], "already_imported": True, "changed": False}
        source = document['text'][section['start']:section['end']].strip()
        source = re.sub(r'^\[(?:文档图片 #\d+：.*|未导入的图片：.*)\]\s*$', '', source, flags=re.M).strip()
        chapter = db.create_chapter(wid, uid, section['title'])
        result = db.update_chapter(chapter['id'], uid, None, source, None, expected_revision=1)
        if not result or result.get('conflict'):
            raise visual_assets.VisualAssetError("章节导入失败")
        conn.execute("INSERT INTO document_chapter_imports(document_id,section_index,chapter_id) VALUES(?,?,?) ON CONFLICT(document_id,section_index) DO UPDATE SET chapter_id=excluded.chapter_id", (document_id, index, chapter['id']))
        return {'chapter_id': chapter['id'], 'title': section['title'], 'already_imported': False,
                'changed': True, 'sidebar_dirty': True, 'summary': f"已原文导入《{section['title']}》"}
