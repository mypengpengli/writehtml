"""Host-side transport for the official Pi Coding Agent runtime."""
import json
import os
import queue
import subprocess
import threading
import time

import config


class PiAgentError(RuntimeError):
    pass


def _pump(stream, kind, output):
    try:
        for line in iter(stream.readline, ""):
            output.put((kind, line))
    finally:
        output.put((kind, None))


def _write_command(process, command):
    if not process.stdin:
        raise PiAgentError("Pi bridge stdin is unavailable")
    process.stdin.write(json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n")
    process.stdin.flush()


def run_turn(request, execute_tool, timeout=None):
    """Run one isolated official Pi Coding Agent process and broker app tools."""
    timeout = float(timeout or config.PI_AGENT_TIMEOUT_SECONDS)
    command = [config.PI_AGENT_NODE, config.PI_AGENT_BRIDGE]
    runtime_dir = os.path.dirname(config.PI_AGENT_BRIDGE)
    try:
        process = subprocess.Popen(
            command,
            cwd=runtime_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise PiAgentError(f"Unable to start Pi Coding Agent ({command[0]}): {exc}") from exc

    events = queue.Queue()
    for kind, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        threading.Thread(target=_pump, args=(stream, kind, events), daemon=True).start()
    stderr = []
    deadline = time.monotonic() + timeout
    try:
        _write_command(process, {"type": "start", "request": request})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PiAgentError(f"Pi Coding Agent timed out after {int(timeout)} seconds")
            try:
                kind, line = events.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if process.poll() is not None:
                    detail = "".join(stderr).strip()
                    raise PiAgentError(detail or f"Pi bridge exited with code {process.returncode}")
                continue

            if kind == "stderr":
                if line and sum(len(part) for part in stderr) < 12000:
                    stderr.append(line)
                continue
            if line is None:
                if process.poll() is not None:
                    detail = "".join(stderr).strip()
                    raise PiAgentError(detail or f"Pi bridge exited with code {process.returncode}")
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PiAgentError(f"Pi bridge emitted invalid JSON: {line[:300]}") from exc

            event_type = event.get("type")
            if event_type == "tool_call":
                call_id = event.get("toolCallId")
                try:
                    if not isinstance(call_id, str) or not isinstance(event.get("name"), str):
                        raise PiAgentError("Pi bridge emitted an invalid tool call")
                    args = event.get("args") if isinstance(event.get("args"), dict) else {}
                    result = execute_tool(event["name"], args)
                    _write_command(process, {"type": "tool_result", "toolCallId": call_id, "result": result})
                except Exception as exc:
                    _write_command(process, {
                        "type": "tool_result", "toolCallId": call_id,
                        "error": str(exc) or exc.__class__.__name__,
                    })
                continue
            if event_type == "done":
                messages = event.get("messages")
                if not isinstance(messages, list):
                    raise PiAgentError("Pi bridge completed without a message transcript")
                return messages
            if event_type in {"fatal", "protocol_error"}:
                raise PiAgentError(str(event.get("error") or "Pi bridge failed"))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
