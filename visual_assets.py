"""Project-owned document imports and reusable, variant-aware visual assets."""
import base64
import hashlib
import io
import json
import time
import warnings

from PIL import Image

import config
import db
import image_generation


VARIANT_KINDS = {"base": "基础", "outfit": "服装", "form": "形态", "state": "状态",
                 "damaged": "损毁", "season": "季节", "logo": "标识", "other": "其他"}


class VisualAssetError(ValueError):
    pass


def migrate(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS document_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, work_id INTEGER NOT NULL,
            name TEXT NOT NULL, source_hash TEXT NOT NULL, text TEXT NOT NULL,
            warnings_json TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
            UNIQUE(user_id, work_id, source_hash)
        );
        CREATE TABLE IF NOT EXISTS resource_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, work_id INTEGER NOT NULL,
            target_type TEXT NOT NULL DEFAULT '', target_id INTEGER,
            image_path TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            variant_kind TEXT NOT NULL DEFAULT 'base', parent_image_id INTEGER, chapter_id INTEGER,
            selected INTEGER NOT NULL DEFAULT 0, document_id INTEGER, source_index INTEGER,
            source_context TEXT NOT NULL DEFAULT '', source_name TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '', width INTEGER, height INTEGER,
            legacy_image_id INTEGER UNIQUE, prompt TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
            size TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(document_id, source_index)
        );
        CREATE TABLE IF NOT EXISTS document_chapter_imports (
            document_id INTEGER NOT NULL, section_index INTEGER NOT NULL, chapter_id INTEGER NOT NULL,
            PRIMARY KEY(document_id,section_index)
        );
        CREATE INDEX IF NOT EXISTS idx_resource_images_target
            ON resource_images(work_id,target_type,target_id,selected DESC,id);
        CREATE INDEX IF NOT EXISTS idx_resource_images_document ON resource_images(document_id,id);
        CREATE TRIGGER IF NOT EXISTS resource_images_legacy_insert AFTER INSERT ON entity_images BEGIN
            INSERT INTO resource_images(user_id,work_id,target_type,target_id,image_path,label,selected,
                legacy_image_id,prompt,model,size,created_at,updated_at)
            VALUES(NEW.user_id,NEW.work_id,'character',NEW.entity_id,NEW.image_path,'角色形象',NEW.selected,
                NEW.id,NEW.prompt,NEW.model,NEW.size,NEW.created_at,NEW.updated_at);
        END;
        CREATE TRIGGER IF NOT EXISTS resource_images_legacy_update AFTER UPDATE ON entity_images BEGIN
            UPDATE resource_images SET selected=NEW.selected,prompt=NEW.prompt,model=NEW.model,
                size=NEW.size,updated_at=NEW.updated_at WHERE legacy_image_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS resource_images_legacy_delete AFTER DELETE ON entity_images BEGIN
            DELETE FROM resource_images WHERE legacy_image_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS resource_images_detach_parent BEFORE DELETE ON resource_images BEGIN
            UPDATE resource_images SET parent_image_id=NULL WHERE parent_image_id=OLD.id;
        END;
        INSERT OR IGNORE INTO resource_images(user_id,work_id,target_type,target_id,image_path,label,
            selected,legacy_image_id,prompt,model,size,created_at,updated_at)
        SELECT user_id,work_id,'character',entity_id,image_path,'角色形象',selected,id,prompt,model,size,
            created_at,updated_at FROM entity_images;
    """)


def _target(conn, uid, wid, target_type, target_id):
    if not db._work_owned(conn, wid, uid):
        raise VisualAssetError("作品不存在")
    if not isinstance(target_type, str) or target_type not in {"character", "card"} or not isinstance(target_id, int) or isinstance(target_id, bool):
        raise VisualAssetError("请选择人物或设定卡")
    if target_type == "character":
        row = conn.execute("SELECT name,'character' AS category FROM entities WHERE id=? AND work_id=? AND kind='人物'", (target_id, wid)).fetchone()
    else:
        row = conn.execute("SELECT name,category FROM production_cards WHERE id=? AND work_id=? AND status='confirmed'", (target_id, wid)).fetchone()
    if not row:
        raise VisualAssetError("目标不存在或不属于当前作品")
    return dict(row)


def _payload(conn, row):
    item = dict(row)
    item.pop("image_path", None)
    item["selected"] = bool(item["selected"])
    item["variant_label"] = VARIANT_KINDS.get(item["variant_kind"], "其他")
    item["target_name"] = "待归类"
    item["category"] = "unbound"
    if item["target_type"]:
        try:
            target = _target(conn, item["user_id"], item["work_id"], item["target_type"], item["target_id"])
            item["target_name"], item["category"] = target["name"], target["category"]
        except VisualAssetError:
            archived = conn.execute("SELECT name,category,status FROM production_cards WHERE id=? AND work_id=?", (item['target_id'], item['work_id'])).fetchone() if item['target_type'] == 'card' else None
            item['target_name'] = archived['name'] if archived else '已删除对象'
            item['category'] = archived['category'] if archived else 'unbound'
            item['target_archived'] = bool(archived and archived['status'] == 'archived')
    return item


def get_image(image_id, uid, include_path=False):
    with db.get_conn() as conn:
        row = conn.execute("SELECT i.* FROM resource_images i JOIN works w ON w.id=i.work_id WHERE i.id=? AND i.user_id=? AND w.user_id=?", (image_id, uid, uid)).fetchone()
        return (dict(row) if include_path else _payload(conn, row)) if row else None


def list_images(wid, uid, target_type=None, target_id=None, document_id=None, unbound=False):
    with db.get_conn() as conn:
        if not db._work_owned(conn, wid, uid):
            raise VisualAssetError("作品不存在")
        where, params = ["work_id=?", "user_id=?"], [wid, uid]
        if target_type is not None:
            _target(conn, uid, wid, target_type, target_id)
            where += ["target_type=?", "target_id=?"]
            params += [target_type, target_id]
        if document_id is not None:
            where.append("document_id=?")
            params.append(document_id)
        if unbound:
            where.append("target_type=''")
        rows = conn.execute("SELECT * FROM resource_images WHERE " + " AND ".join(where) + " ORDER BY selected DESC,id DESC", params).fetchall()
        return [_payload(conn, row) for row in rows]


def image_info(data):
    if not data or len(data) > config.ENTITY_IMAGE_MAX_BYTES:
        raise VisualAssetError("图片为空或超过大小限制")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > 40_000_000:
                    raise VisualAssetError("图片像素过大，请缩小后上传")
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF", "AVIF"}:
                    raise VisualAssetError("图片格式不支持，请转为 PNG、JPEG、WebP、GIF 或 AVIF")
                image.verify()
        return width, height
    except VisualAssetError:
        raise
    except Exception as exc:
        raise VisualAssetError("图片损坏或不是受支持的位图") from exc


def add_image(wid, uid, data, values=None):
    values = values or {}
    width, height = image_info(data)
    path = None
    try:
        with db.atomic_transaction(immediate=True) as conn:
            if not db._work_owned(conn, wid, uid):
                raise VisualAssetError("作品不存在")
            path, _ = image_generation.save_image(uid, 0, data)
            now = time.time()
            cur = conn.execute("INSERT INTO resource_images(user_id,work_id,image_path,label,source_name,source_context,document_id,source_index,content_hash,width,height,prompt,model,size,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, wid, path, str(values.get("label") or "图片")[:200], str(values.get("source_name") or "")[:240],
                 str(values.get("source_context") or "")[:2000], values.get("document_id"), values.get("source_index"),
                 hashlib.sha256(data).hexdigest(), width, height, str(values.get('prompt') or '')[:8000],
                 str(values.get('model') or '')[:200], str(values.get('size') or '')[:32], now, now))
            image_id = cur.lastrowid
            if values.get("target_type"):
                bind_image(image_id, uid, values)
        return get_image(image_id, uid)
    except Exception:
        if path:
            image_generation.remove_image(path)
        raise


def bind_image(image_id, uid, values):
    if not isinstance(values, dict):
        raise VisualAssetError("图片信息格式无效")
    with db.atomic_transaction(immediate=True) as conn:
        row = get_image(image_id, uid, include_path=True)
        if not row:
            raise VisualAssetError("图片不存在")
        target_type = values.get("target_type", row["target_type"])
        target_id = values.get("target_id", row["target_id"])
        _target(conn, uid, row["work_id"], target_type, target_id)
        if row["target_type"] and (target_type, target_id) != (row["target_type"], row["target_id"]):
            raise VisualAssetError("图片已绑定其他对象；如需复用，请复制图片后绑定，不要改写原归属")
        variant = values.get("variant_kind", row["variant_kind"])
        if variant not in VARIANT_KINDS:
            raise VisualAssetError("衍生类型无效")
        chapter_id = values.get("chapter_id", row["chapter_id"])
        if chapter_id is not None and (not isinstance(chapter_id, int) or isinstance(chapter_id, bool) or not db._chapter_for_work(conn, chapter_id, row["work_id"])):
            raise VisualAssetError("章节不属于当前作品")
        parent_id = values.get("parent_image_id", row["parent_image_id"])
        if parent_id is not None:
            if not isinstance(parent_id, int) or isinstance(parent_id, bool):
                raise VisualAssetError("原图编号无效")
            parent = get_image(parent_id, uid, include_path=True)
            if not parent or parent["work_id"] != row["work_id"] or (parent["target_type"], parent["target_id"]) != (target_type, target_id):
                raise VisualAssetError("衍生原图必须属于同一个人物或设定")
            visited = {image_id}
            while parent:
                if parent["id"] in visited:
                    raise VisualAssetError("衍生关系不能形成循环")
                visited.add(parent["id"])
                parent = get_image(parent["parent_image_id"], uid, True) if parent["parent_image_id"] else None
        label = values.get("label", row["label"])
        description = values.get("description", row["description"])
        if not isinstance(label, str) or not label.strip() or len(label) > 200 or not isinstance(description, str) or len(description) > 4000:
            raise VisualAssetError("图片名称或说明无效")
        legacy_id = row["legacy_image_id"]
        if target_type == "character" and legacy_id is None:
            now = time.time()
            cur = conn.execute("INSERT INTO entity_images(user_id,work_id,entity_id,image_path,prompt,model,size,selected,created_at,updated_at) VALUES(?,?,?,?,?,?,?,0,?,?)", (uid, row["work_id"], target_id, row["image_path"], row['prompt'] or description, row['model'], row['size'], now, now))
            legacy_id = cur.lastrowid
            # Keep the document image's stable ID; the legacy insert trigger creates a temporary mirror.
            conn.execute("DELETE FROM resource_images WHERE legacy_image_id=?", (legacy_id,))
        conn.execute("UPDATE resource_images SET target_type=?,target_id=?,label=?,description=?,variant_kind=?,parent_image_id=?,chapter_id=?,legacy_image_id=?,updated_at=? WHERE id=?",
                     (target_type, target_id, label.strip(), description, variant, parent_id, chapter_id, legacy_id, time.time(), image_id))
        if values.get("set_primary") is True:
            select_image(image_id, uid)
    return get_image(image_id, uid)


def select_image(image_id, uid):
    with db.atomic_transaction(immediate=True) as conn:
        row = get_image(image_id, uid, True)
        if not row or not row["target_type"]:
            raise VisualAssetError("请先绑定图片归属")
        _target(conn, uid, row["work_id"], row["target_type"], row["target_id"])
        if row["legacy_image_id"]:
            db.select_entity_image(row["legacy_image_id"], uid)
        else:
            conn.execute("UPDATE resource_images SET selected=0 WHERE work_id=? AND target_type=? AND target_id=?", (row["work_id"], row["target_type"], row["target_id"]))
            conn.execute("UPDATE resource_images SET selected=1,updated_at=? WHERE id=?", (time.time(), image_id))
    return get_image(image_id, uid)


def delete_image(image_id, uid):
    with db.atomic_transaction(immediate=True) as conn:
        row = get_image(image_id, uid, True)
        if not row:
            raise VisualAssetError("图片不存在")
        if row["legacy_image_id"]:
            db.delete_entity_image_asset(row["legacy_image_id"], uid)
        else:
            conn.execute("DELETE FROM resource_images WHERE id=?", (image_id,))
    image_generation.remove_image(row["image_path"])


def copy_image(image_id, uid):
    row = get_image(image_id, uid, True)
    if not row:
        raise VisualAssetError("图片不存在")
    data = image_generation.resolve_image_path(row["image_path"]).read_bytes()
    return add_image(row["work_id"], uid, data, {"label": row["label"] + "（副本）", "source_context": row["source_context"], "source_name": row["source_name"]})


def inspect_image(image_id, uid):
    row = get_image(image_id, uid, True)
    if not row:
        raise VisualAssetError("图片不存在")
    data = image_generation.resolve_image_path(row["image_path"]).read_bytes()
    image_info(data)
    # Send a bounded preview to the model; preserve the original stored image for downloads.
    with Image.open(io.BytesIO(data)) as image:
        image.thumbnail((1536, 1536))
        rgba = image.convert('RGBA')
        image = Image.alpha_composite(Image.new('RGBA', rgba.size, '#eeeeee'), rgba).convert('RGB')
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
    return {"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(output.getvalue()).decode("ascii")}


def get_document(document_id, uid, wid=None):
    with db.get_conn() as conn:
        row = conn.execute("SELECT d.* FROM document_imports d JOIN works w ON w.id=d.work_id WHERE d.id=? AND d.user_id=? AND w.user_id=?", (document_id, uid, uid)).fetchone()
        if not row or (wid is not None and row["work_id"] != wid):
            return None
        result = dict(row)
        result["warnings"] = json.loads(result.pop("warnings_json"))
        result["images"] = list_images(result["work_id"], uid, document_id=document_id)
        return result
