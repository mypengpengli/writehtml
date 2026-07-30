"""Pi Coding Agent integration smoke test using a local OpenAI-compatible SSE server."""
import base64
import json
import os
import shutil
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smoke_tmp", "pi-" + uuid.uuid4().hex[:10])
os.makedirs(TMP, exist_ok=True)
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["PI_AGENT_ENABLED"] = "true"
os.environ["PI_AGENT_TIMEOUT_SECONDS"] = "30"
os.environ["SIGNUP_CODE"] = ""
os.environ["ALLOW_SIGNUP"] = "true"

from fastapi.testclient import TestClient
import db
import main


def ok(condition, message):
    print(("  OK  " if condition else " FAIL ") + message)
    if not condition:
        raise SystemExit(1)


def post_stream_events(client, path, payload, token):
    events = []
    with client.stream(
        "POST", path, json=payload,
        headers={"Authorization": "Bearer " + token},
    ) as response:
        status = response.status_code
        headers = dict(response.headers)
        for line in response.iter_lines():
            if line:
                events.append(json.loads(line))
    return status, headers, events


def sse_chunk(delta, finish_reason=None, response_id="pi-test"):
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "pi-test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


class FakeOpenAI(BaseHTTPRequestHandler):
    requests = []
    skill_id = None

    def do_POST(self):
        size = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(size))
        type(self).requests.append(payload)
        prompt_text = json.dumps(payload.get("messages", []), ensure_ascii=False)
        has_native_bash_result = any(
            message.get("role") == "tool" and "PI_NATIVE_BASH_OK" in str(message.get("content", ""))
            for message in payload.get("messages", []) if isinstance(message, dict)
        )
        has_audio = any(
            message.get("role") == "user" and isinstance(message.get("content"), list)
            and any(part.get("type") == "input_audio" for part in message["content"] if isinstance(part, dict))
            for message in payload.get("messages", []) if isinstance(message, dict)
        )
        if has_audio:
            chunks = [sse_chunk({"role": "assistant", "content": "Pi received raw audio."}), sse_chunk({}, "stop")]
        elif "Run native Pi shell probe" in prompt_text and not has_native_bash_result:
            chunks = [
                sse_chunk({"role": "assistant", "tool_calls": [{
                    "index": 0,
                    "id": "call_native_bash",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "echo PI_NATIVE_BASH_OK"})},
                }]}),
                sse_chunk({}, "tool_calls"),
            ]
        elif "Run native Pi shell probe" in prompt_text:
            chunks = [sse_chunk({"role": "assistant", "content": "Pi executed native bash."}), sse_chunk({}, "stop")]
        elif len(type(self).requests) == 1:
            chunks = [
                sse_chunk({"role": "assistant", "tool_calls": [{
                    "index": 0,
                    "id": "call_activate",
                    "type": "function",
                    "function": {"name": "activate_skill", "arguments": json.dumps({"skill_id": type(self).skill_id})},
                }]}),
                sse_chunk({}, "tool_calls"),
            ]
        else:
            chunks = [sse_chunk({"role": "assistant", "content": "Pi completed the Skill turn."}), sse_chunk({}, "stop")]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


db.init_db()
server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    user = db.create_user("piuser", "pw1234")
    uid = user["id"]
    token = "pi-test-token"
    main._sessions[token] = uid
    work = db.create_work(uid, "Pi work")
    chapter = db.create_chapter(work["id"], uid, "Chapter")
    db.update_chapter(chapter["id"], uid, None, "Original chapter text", None)
    skill = db.create_agent_skill(
        uid, None, "pi-skill", "test dynamic skill", "PI_SKILL_RULE: preserve narrative viewpoint",
        source_kind="skill_md",
    )
    FakeOpenAI.skill_id = skill["id"]
    db.save_settings(uid, f"http://127.0.0.1:{server.server_port}/v1", "test-key", "pi-test-model")
    client = TestClient(main.app)
    response = client.post(
        "/api/agent", json={"text": "Use pi-skill", "chapter_id": chapter["id"]},
        headers={"Authorization": "Bearer " + token},
    )
    ok(response.status_code == 200, "Pi Agent API turn succeeds")
    payload = response.json()
    ok(payload["reply"] == "Pi completed the Skill turn.", "Pi final answer returned")
    ok(any(message.get("role") == "tool" for message in payload["messages"]), "Pi tool result is converted for the UI")
    ok(len(FakeOpenAI.requests) == 2, "Pi makes a second model call after a tool result")
    second_prompt = json.dumps(FakeOpenAI.requests[1], ensure_ascii=False)
    ok("PI_SKILL_RULE" in second_prompt, "activate_skill injects SKILL.md into the next Pi turn")
    stored = db.get_conversation(uid, chapter["id"])
    ok(any(message.get("role") == "toolResult" for message in stored["messages"]), "database stores Pi-native toolResult")
    ok(json.dumps(stored["messages"], ensure_ascii=False).count("Use pi-skill") == 1,
       "Pi successful turn stores the user input only once")
    ok("PI_SKILL_RULE" not in json.dumps(stored["messages"], ensure_ascii=False), "Skill text is not persisted in chat history")
    fetched = client.get(
        f"/api/agent/conversation?chapter_id={chapter['id']}",
        headers={"Authorization": "Bearer " + token},
    ).json()
    ok(any(message.get("role") == "tool" for message in fetched["messages"]), "history endpoint remains UI compatible")
    stream_chapter = db.create_chapter(work["id"], uid, "Streaming Pi chapter")
    stream_status, stream_headers, stream_events = post_stream_events(
        client, "/api/agent/stream",
        {"text": "Stream a Pi reply", "chapter_id": stream_chapter["id"]}, token,
    )
    stream_result = next((event.get("data") for event in stream_events if event.get("type") == "result"), None)
    ok(stream_status == 200 and stream_headers.get("x-accel-buffering") == "no",
       "Pi streaming endpoint disables reverse-proxy buffering")
    ok(any(event.get("type") == "assistant_delta" for event in stream_events),
       "Pi text delta reaches the HTTP stream")
    ok(stream_result and stream_result["reply"] == "Pi completed the Skill turn.",
       "Pi stream ends with the authoritative complete result")
    failed_chapter = db.create_chapter(work["id"], uid, "Failed Pi chapter")
    original_pi_run_turn = main.pi_agent.run_turn
    def fail_pi_turn(*args, **kwargs):
        raise main.pi_agent.PiAgentError("simulated provider network failure")
    main.pi_agent.run_turn = fail_pi_turn
    try:
        _, _, failed_events = post_stream_events(
            client, "/api/agent/stream",
            {"text": "Keep this even if Pi fails", "chapter_id": failed_chapter["id"]}, token,
        )
    finally:
        main.pi_agent.run_turn = original_pi_run_turn
    failed_stored = db.get_conversation(uid, failed_chapter["id"])
    ok(any(event.get("type") == "error" for event in failed_events),
       "Pi provider failure reaches the HTTP stream")
    ok(json.dumps(failed_stored["messages"], ensure_ascii=False).count("Keep this even if Pi fails") == 1,
       "Pi provider failure still preserves the user input")
    _, _, resumed_events = post_stream_events(
        client, "/api/agent/stream",
        {"text": "Continue after the failed turn", "chapter_id": failed_chapter["id"]}, token,
    )
    resumed_result = next(
        (event.get("data") for event in resumed_events if event.get("type") == "result"), None
    )
    resumed_stored = db.get_conversation(uid, failed_chapter["id"])
    resumed_serialized = json.dumps(resumed_stored["messages"], ensure_ascii=False)
    ok(
        resumed_result
        and resumed_serialized.count("Keep this even if Pi fails") == 1
        and resumed_serialized.count("Continue after the failed turn") == 1,
        "Pi next turn keeps failed and current user inputs without duplication",
    )
    voice = client.post(
        "/api/agent/audio", json={
            "audio": base64.b64encode(b"fake-pi-audio").decode(), "format": "wav", "chapter_id": chapter["id"],
        }, headers={"Authorization": "Bearer " + token},
    )
    ok(voice.status_code == 200 and voice.json()["reply"] == "Pi received raw audio.", "Pi direct audio turn succeeds")
    raw_audio_prompt = FakeOpenAI.requests[-1]
    ok("input_audio" in json.dumps(raw_audio_prompt), "raw audio reaches the OpenAI-compatible model request")
    stored_after_voice = db.get_conversation(uid, chapter["id"])
    serialized = json.dumps(stored_after_voice["messages"], ensure_ascii=False)
    ok("ZmFrZS1waS1hdWRpbw==" not in serialized, "raw audio Base64 is never persisted")
    audio_stream_chapter = db.create_chapter(work["id"], uid, "Streaming audio chapter")
    audio_status, _, audio_events = post_stream_events(
        client, "/api/agent/audio/stream", {
            "audio": base64.b64encode(b"stream-pi-audio").decode(),
            "format": "wav", "chapter_id": audio_stream_chapter["id"],
        }, token,
    )
    audio_result = next((event.get("data") for event in audio_events if event.get("type") == "result"), None)
    ok(audio_status == 200 and any(
        event.get("type") == "assistant_delta" and "Pi received raw audio" in event.get("delta", "")
        for event in audio_events
    ), "Pi direct audio reply is streamed")
    ok(audio_result and audio_result.get("voice", {}).get("mode") == "direct",
       "Pi audio stream keeps direct-voice diagnostics")
    plain_chapter = db.create_chapter(work["id"], uid, "Plain Pi chapter")
    plain = client.post(
        "/api/agent", json={"text": "Plain Pi reply", "chapter_id": plain_chapter["id"]},
        headers={"Authorization": "Bearer " + token},
    )
    ok(plain.status_code == 200, "Pi text-only turn succeeds")
    plain_stored = db.get_conversation(uid, plain_chapter["id"])
    ok(not any(message.get("role") == "toolResult" for message in plain_stored["messages"]), "plain Pi transcript has no tool result")
    plain_history = client.get(
        f"/api/agent/conversation?chapter_id={plain_chapter['id']}",
        headers={"Authorization": "Bearer " + token},
    ).json()
    ok(all(isinstance(message.get("content"), str) for message in plain_history["messages"]),
       "text-only Pi history is converted for the existing UI")
    native_chapter = db.create_chapter(work["id"], uid, "Native Pi chapter")
    native = client.post(
        "/api/agent", json={"text": "Run native Pi shell probe", "chapter_id": native_chapter["id"]},
        headers={"Authorization": "Bearer " + token},
    )
    ok(native.status_code == 200 and native.json()["reply"] == "Pi executed native bash.",
       "Pi native bash tool executes through the writing agent")
    native_prompt = json.dumps(FakeOpenAI.requests[-1], ensure_ascii=False)
    ok("PI_NATIVE_BASH_OK" in native_prompt, "native bash stdout returns to the model")
    native_skill_root = os.path.join(TMP, "native-pi-skills")
    native_skill_dir = os.path.join(native_skill_root, "native-pi-skill")
    os.makedirs(native_skill_dir, exist_ok=True)
    with open(os.path.join(native_skill_dir, "SKILL.md"), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "---\n"
            "name: native-pi-skill\n"
            "description: Native Pi Skill discovery probe\n"
            "---\n"
            "NATIVE_PI_SKILL_BODY\n"
        )
    old_pi_skill_dir = main.config.PI_AGENT_SKILL_DIR
    main.config.PI_AGENT_SKILL_DIR = native_skill_root
    try:
        native_skill_chapter = db.create_chapter(work["id"], uid, "Native Pi skill chapter")
        native_skill_response = client.post(
            "/api/agent", json={"text": "Use native Pi Skill", "chapter_id": native_skill_chapter["id"]},
            headers={"Authorization": "Bearer " + token},
        )
        ok(native_skill_response.status_code == 200, "Pi native Skill turn succeeds")
        native_skill_prompt = json.dumps(FakeOpenAI.requests[-1], ensure_ascii=False)
        ok("native-pi-skill" in native_skill_prompt and "Native Pi Skill discovery probe" in native_skill_prompt,
           "PI_AGENT_SKILL_DIR is discovered by native Pi")
    finally:
        main.config.PI_AGENT_SKILL_DIR = old_pi_skill_dir
    runtime_root = os.path.join(TMP, "pi-local-skill")
    meta_dir = os.path.join(runtime_root, "meta-memory")
    os.makedirs(meta_dir, exist_ok=True)
    with open(os.path.join(meta_dir, "SKILL.md"), "w", encoding="utf-8", newline="\n") as stream:
        stream.write("PI_LOCAL_SKILL_MARKER\n")
    launcher = os.path.join(TMP, "pi_launcher.py")
    with open(launcher, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "import json, sys\n"
            "phase = sys.argv[1] if len(sys.argv) > 1 else ''\n"
            "print(json.dumps({'status': 'ok', 'context': 'PI_LAUNCHER_CONTEXT'} if phase == 'before' else {'status': 'ok'}))\n"
        )
    old_runtime = {
        "dir": main.config.AGENT_SKILL_DIR, "launcher": main.config.AGENT_SKILL_LAUNCHER,
        "cwd": main.config.AGENT_SKILL_CWD, "runtime_dir": main.config.AGENT_SKILL_RUNTIME_DIR,
        "touch": main.config.AGENT_SKILL_TOUCH_SECONDS,
    }
    main.config.AGENT_SKILL_DIR = runtime_root
    main.config.AGENT_SKILL_LAUNCHER = [sys.executable, launcher]
    main.config.AGENT_SKILL_CWD = os.path.dirname(os.path.abspath(__file__))
    main.config.AGENT_SKILL_RUNTIME_DIR = os.path.join(TMP, "pi-skill-turns")
    main.config.AGENT_SKILL_TOUCH_SECONDS = 60
    try:
        runtime_chapter = db.create_chapter(work["id"], uid, "Pi runtime chapter")
        runtime_response = client.post(
            "/api/agent", json={"text": "Use local runtime", "chapter_id": runtime_chapter["id"]},
            headers={"Authorization": "Bearer " + token},
        )
        ok(runtime_response.status_code == 200 and runtime_response.json().get("skill_runtime", {}).get("state") == "ok",
           "Pi turn is confirmed by the local launcher")
        runtime_prompt = json.dumps(FakeOpenAI.requests[-1], ensure_ascii=False)
        ok("PI_LOCAL_SKILL_MARKER" in runtime_prompt and "PI_LAUNCHER_CONTEXT" in runtime_prompt,
           "Pi receives local SKILL.md and launcher before context")
        runtime_stored = db.get_conversation(uid, runtime_chapter["id"])
        ok("PI_LOCAL_SKILL_MARKER" not in json.dumps(runtime_stored["messages"], ensure_ascii=False),
           "local Skill text is not persisted by Pi")
        buffered_chapter = db.create_chapter(work["id"], uid, "Buffered stream chapter")
        buffered_status, _, buffered_events = post_stream_events(
            client, "/api/agent/stream",
            {"text": "Use buffered local runtime", "chapter_id": buffered_chapter["id"]}, token,
        )
        ok(buffered_status == 200 and not any(
            event.get("type") == "assistant_delta" for event in buffered_events
        ), "launcher 回合不会提前流出未确认回答")
        ok(any(
            event.get("type") == "status" and event.get("stage") == "confirming"
            for event in buffered_events
        ) and any(event.get("type") == "result" for event in buffered_events),
           "launcher 确认后才返回流式回合的最终结果")
    finally:
        main.config.AGENT_SKILL_DIR = old_runtime["dir"]
        main.config.AGENT_SKILL_LAUNCHER = old_runtime["launcher"]
        main.config.AGENT_SKILL_CWD = old_runtime["cwd"]
        main.config.AGENT_SKILL_RUNTIME_DIR = old_runtime["runtime_dir"]
        main.config.AGENT_SKILL_TOUCH_SECONDS = old_runtime["touch"]
finally:
    server.shutdown()
    server.server_close()
    shutil.rmtree(TMP, ignore_errors=True)

print("Pi Coding Agent smoke test passed.")
