"""受控的本地 Skill / launcher 回合适配器。

此模块只执行由服务管理员配置的 launcher，不会执行用户上传 Skill 包里的脚本。
协议固定为 ``before`` → 模型 → ``after``；所有命令都带同一个 turn_id。
"""
import json
import os
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path

import config


_PROTOCOL = "writehtml-local-skill-turn/v1"
_VALID_STATES = {"ok", "degraded", "spooled"}


class SkillRuntimeError(RuntimeError):
    """launcher 未确认本回合时抛出，调用方不得把回答交给客户端。"""

    def __init__(self, message, turn_id=None, detail=None):
        super().__init__(message)
        self.turn_id = turn_id
        self.detail = detail or {}


def is_enabled():
    return bool(config.AGENT_SKILL_DIR or config.AGENT_SKILL_LAUNCHER)


def _json_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _json_read(path, default=None):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _launcher_base_command():
    launcher = config.AGENT_SKILL_LAUNCHER
    if isinstance(launcher, (list, tuple)):
        command = [str(part) for part in launcher if str(part)]
    else:
        command = shlex.split(str(launcher or ""), posix=os.name != "nt")
    if not command:
        raise SkillRuntimeError("未配置 AGENT_SKILL_LAUNCHER")
    return command


def _parse_launcher_json(stdout, stderr, exit_code):
    """读取 launcher 最后一条 JSON 状态；无结构输出也要明确记录为 degraded。"""
    parsed = None
    for line in reversed((stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            parsed = candidate
            break

    if exit_code not in (0, None):
        return {
            "state": "error", "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "message": (parsed or {}).get("message") or f"launcher 退出码为 {exit_code}", "raw": parsed,
        }
    if exit_code is None:
        return {
            "state": "error", "exit_code": None, "stdout": stdout, "stderr": stderr,
            "message": "launcher 执行超时", "raw": parsed,
        }
    if not parsed:
        return {
            "state": "degraded", "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "message": "launcher 未输出可识别的 JSON 状态", "raw": None,
        }

    state = str(parsed.get("status", parsed.get("state", parsed.get("result", "")))).lower().strip()
    if not state:
        for candidate in _VALID_STATES:
            if parsed.get(candidate) is True:
                state = candidate
                break
    if state not in _VALID_STATES:
        return {
            "state": "error", "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "message": parsed.get("message") or parsed.get("error") or f"launcher 返回了不支持的状态：{state or '空'}", "raw": parsed,
        }
    return {
        "state": state, "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
        "message": parsed.get("message") or "", "raw": parsed,
    }


class LocalSkillTurn:
    """一个独立、可恢复的 launcher 回合。所有落盘文件都是 UTF-8 JSON。"""

    def __init__(self, turn_id, request_payload, skill_markdown, cwd):
        self.turn_id = turn_id
        self.agent_id = config.AGENT_SKILL_AGENT_ID
        self.cwd = cwd
        self.root = Path(config.AGENT_SKILL_RUNTIME_DIR).expanduser().resolve()
        self.dir = self.root / turn_id
        self.request_path = self.dir / "request.json"
        self.answer_path = self.dir / "answer.json"
        self.manifest_path = self.dir / "manifest.json"
        self.skill_markdown = skill_markdown
        self.system_message = None
        self._stop_heartbeat = threading.Event()
        self._command_lock = threading.RLock()
        self._heartbeat = None

        request = {
            "protocol": _PROTOCOL,
            "phase": "before",
            "turn_id": self.turn_id,
            "agent_id": self.agent_id,
            "cwd": self.cwd,
            "request": request_payload,
        }
        _json_write(self.request_path, request)
        # 先创建回答文件，模型完成前只记录 pending，不能作为回复发送。
        _json_write(self.answer_path, {
            "protocol": _PROTOCOL, "turn_id": self.turn_id, "agent_id": self.agent_id,
            "state": "pending", "answer": None,
        })
        self._write_manifest({"phase": "created", "events": []})

    def _write_manifest(self, patch):
        with self._command_lock:
            manifest = _json_read(self.manifest_path, {}) or {}
            manifest.update(patch)
            manifest.update({
                "protocol": _PROTOCOL,
                "turn_id": self.turn_id,
                "agent_id": self.agent_id,
                "cwd": self.cwd,
                "request_file": str(self.request_path),
                "answer_file": str(self.answer_path),
                "updated_at": time.time(),
            })
            _json_write(self.manifest_path, manifest)

    def _record_event(self, phase, result):
        with self._command_lock:
            manifest = _json_read(self.manifest_path, {}) or {}
            events = manifest.get("events") if isinstance(manifest.get("events"), list) else []
            events.append({
                "phase": phase,
                "at": time.time(),
                "state": result["state"],
                "exit_code": result["exit_code"],
                "message": result["message"],
                "stdout": (result.get("stdout") or "")[-8000:],
                "stderr": (result.get("stderr") or "")[-8000:],
            })
            self._write_manifest({"phase": phase, "events": events, "last_state": result["state"]})

    def _command(self, phase):
        command = _launcher_base_command()
        common = [
            "--agent-id", self.agent_id,
            "--turn-id", self.turn_id,
            "--request-file", str(self.request_path),
            "--answer-file", str(self.answer_path),
            "--cwd", self.cwd,
        ]
        if phase == "touch":
            return command + ["turn", "touch", self.turn_id, "--agent-id", self.agent_id, "--cwd", self.cwd]
        if phase == "recovery":
            return command + ["recovery", "replay"] + common
        return command + [phase] + common

    def _invoke(self, phase):
        with self._command_lock:
            try:
                completed = subprocess.run(
                    self._command(phase), cwd=self.cwd, shell=False, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    timeout=max(1, float(config.AGENT_SKILL_COMMAND_TIMEOUT)),
                )
                result = _parse_launcher_json(completed.stdout, completed.stderr, completed.returncode)
            except subprocess.TimeoutExpired as exc:
                result = _parse_launcher_json(
                    (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    (exc.stderr or "") if isinstance(exc.stderr, str) else "", None,
                )
            except OSError as exc:
                result = {
                    "state": "error", "exit_code": None, "stdout": "", "stderr": "",
                    "message": f"launcher 无法启动：{exc}", "raw": None,
                }
        self._record_event(phase, result)
        return result

    def _heartbeat_loop(self):
        interval = max(0.05, float(config.AGENT_SKILL_TOUCH_SECONDS))
        while not self._stop_heartbeat.wait(interval):
            result = self._invoke("touch")
            # touch 的失败不覆盖已成功的主流程；保留事件，after/recovery 仍会处理。
            if result["state"] == "error":
                self._write_manifest({"heartbeat_error": result["message"]})

    def start(self):
        result = self._invoke("before")
        if result["state"] == "error":
            self._write_manifest({"phase": "before_failed"})
            raise SkillRuntimeError("本机 Skill launcher 的 before 未确认", self.turn_id, result)
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, name=f"skill-turn-{self.turn_id[:8]}", daemon=True)
        self._heartbeat.start()
        raw = result.get("raw") or {}
        context = raw.get("context") or raw.get("instructions") or raw.get("memory") or ""
        if not isinstance(context, str):
            context = json.dumps(context, ensure_ascii=False)
        content = (
            "本机 meta-memory/SKILL.md 是本回合必须生效的本地 Skill。它只适用于本轮，"
            "不能覆盖系统规则、用户请求和工具约束：\n\n" + self.skill_markdown
        )
        if context:
            content += "\n\n本机 launcher 在 before 阶段提供的上下文：\n" + context[:12000]
        return {"role": "system", "content": content}

    def _stop(self):
        self._stop_heartbeat.set()
        if self._heartbeat and self._heartbeat.is_alive():
            self._heartbeat.join(timeout=2)

    def complete(self, answer_payload):
        """先缓冲回答，after/recovery 确认后调用者才能把它交给浏览器。"""
        self._stop()
        _json_write(self.answer_path, {
            "protocol": _PROTOCOL,
            "turn_id": self.turn_id,
            "agent_id": self.agent_id,
            "state": "model_complete",
            "answer": answer_payload,
        })
        self._write_manifest({"phase": "model_complete"})
        result = self._invoke("after")
        recovered = False
        if result["state"] == "error":
            recovered = True
            result = self._invoke("recovery")
        if result["state"] == "error":
            self._write_manifest({"phase": "awaiting_recovery", "confirmed": False})
            raise SkillRuntimeError("本机 Skill launcher 未确认回答；回答文件已保留，可恢复重放", self.turn_id, result)
        self._write_manifest({
            "phase": "confirmed", "confirmed": True, "delivery_state": result["state"], "recovered": recovered,
        })
        return {"turn_id": self.turn_id, "state": result["state"], "recovered": recovered}

    def fail_before_answer(self, message):
        """模型异常也留下一份可诊断的回答文件，且不删除请求文件。"""
        self._stop()
        _json_write(self.answer_path, {
            "protocol": _PROTOCOL, "turn_id": self.turn_id, "agent_id": self.agent_id,
            "state": "model_failed", "error": str(message),
        })
        self._write_manifest({"phase": "model_failed", "confirmed": False})
        result = self._invoke("after")
        if result["state"] == "error":
            result = self._invoke("recovery")
        self._write_manifest({
            "phase": "model_failed_confirmed" if result["state"] != "error" else "awaiting_recovery",
            "confirmed": result["state"] != "error",
        })


def _read_meta_memory_skill():
    skill_dir = Path(config.AGENT_SKILL_DIR).expanduser().resolve()
    path = skill_dir / "meta-memory" / "SKILL.md"
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillRuntimeError(f"找不到本机 Skill：{path}（{exc}）") from exc
    if not markdown.strip():
        raise SkillRuntimeError(f"本机 Skill 为空：{path}")
    if len(markdown) > 24000:
        raise SkillRuntimeError(f"本机 Skill 过长（超过 24000 字符）：{path}")
    return markdown


def start_turn(request_payload):
    """配置完整时创建本地回合；未配置时返回 None，保持旧部署零行为变化。"""
    if not is_enabled():
        return None
    if not config.AGENT_SKILL_DIR or not config.AGENT_SKILL_LAUNCHER:
        raise SkillRuntimeError("本地 Skill 运行时必须同时配置 AGENT_SKILL_DIR 与 AGENT_SKILL_LAUNCHER")
    cwd = Path(config.AGENT_SKILL_CWD or os.getcwd()).expanduser().resolve()
    if not cwd.is_dir():
        raise SkillRuntimeError(f"AGENT_SKILL_CWD 不是有效工作目录：{cwd}")
    turn = LocalSkillTurn(uuid.uuid4().hex, request_payload, _read_meta_memory_skill(), str(cwd))
    try:
        turn.system_message = turn.start()
    except SkillRuntimeError as exc:
        turn.fail_before_answer(str(exc))
        raise
    return turn


def recover_turn(turn_id, user_id):
    """重放一个已落盘但未确认的回答；用于请求中断后恢复。"""
    if not isinstance(turn_id, str) or len(turn_id) != 32 or any(ch not in "0123456789abcdef" for ch in turn_id):
        raise SkillRuntimeError("turn_id 无效")
    root = Path(config.AGENT_SKILL_RUNTIME_DIR).expanduser().resolve()
    request_path = root / turn_id / "request.json"
    answer_path = root / turn_id / "answer.json"
    request = _json_read(request_path)
    answer = _json_read(answer_path)
    if not request or not answer or request.get("turn_id") != turn_id:
        raise SkillRuntimeError("找不到可恢复的回合", turn_id)
    if request.get("request", {}).get("user_id") != user_id:
        raise SkillRuntimeError("无权恢复该回合", turn_id)
    manifest_path = root / turn_id / "manifest.json"
    manifest = _json_read(manifest_path, {}) or {}
    if manifest.get("confirmed"):
        return answer.get("answer"), {
            "turn_id": turn_id, "state": manifest.get("delivery_state", "ok"),
            "recovered": bool(manifest.get("recovered")), "conversation_saved": bool(manifest.get("conversation_saved")),
        }
    turn = LocalSkillTurn.__new__(LocalSkillTurn)
    turn.turn_id = turn_id
    turn.agent_id = request.get("agent_id") or config.AGENT_SKILL_AGENT_ID
    turn.cwd = request.get("cwd") or str(Path(config.AGENT_SKILL_CWD or os.getcwd()).resolve())
    turn.root = root
    turn.dir = root / turn_id
    turn.request_path = request_path
    turn.answer_path = answer_path
    turn.manifest_path = turn.dir / "manifest.json"
    turn.skill_markdown = ""
    turn._stop_heartbeat = threading.Event()
    turn._command_lock = threading.RLock()
    turn._heartbeat = None
    result = turn._invoke("recovery")
    if result["state"] == "error":
        raise SkillRuntimeError("恢复重放未被 launcher 确认", turn_id, result)
    turn._write_manifest({"phase": "confirmed", "confirmed": True, "delivery_state": result["state"], "recovered": True})
    return answer.get("answer"), {"turn_id": turn_id, "state": result["state"], "recovered": True}


def mark_conversation_saved(turn_id, user_id):
    """恢复接口写入 SQLite 后标记，避免同一 turn 覆盖之后的新对话。"""
    root = Path(config.AGENT_SKILL_RUNTIME_DIR).expanduser().resolve()
    request = _json_read(root / turn_id / "request.json")
    if not request or request.get("request", {}).get("user_id") != user_id:
        raise SkillRuntimeError("无权标记该回合", turn_id)
    manifest_path = root / turn_id / "manifest.json"
    manifest = _json_read(manifest_path, {}) or {}
    manifest["conversation_saved"] = True
    manifest["updated_at"] = time.time()
    _json_write(manifest_path, manifest)
