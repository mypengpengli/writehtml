# Pi Agent Core Runtime

The writing agent runs on the official `@earendil-works/pi-agent-core` package,
version `0.81.1`. Python remains the authority for authentication, works,
chapters, revisions, and the allowlisted writing tools.

## Execution Boundary

- Pi owns the model loop, transcript state, sequential tool scheduling, and
  per-conversation session identity.
- `pi_runtime/bridge.mjs` is a JSONL stdio transport. It never exposes a shell
  or an arbitrary command tool to the model.
- `pi_agent.py` launches one bridge process per request, routes only declared
  writing tools back to Python, and kills the process at timeout.
- Pi-native messages are persisted in `agent_conversations`; the HTTP API
  translates them to the existing `user`, `assistant`, and `tool` UI format.

## Skills

The agent first receives only the Skill catalog. `activate_skill` returns the
full `SKILL.md` only for the current Pi turn; `read_skill_resource` does the
same for imported text resources. These private system additions are removed
before the Pi transcript is persisted.

## Voice Input

Pi Agent Core's public model contract accepts text and images. For the existing
direct-voice feature, the bridge provides a narrow OpenAI-compatible
`input_audio` stream adapter while Pi still owns the Agent loop and tool calls.
The audio payload is stripped before the Pi transcript is returned or saved.
Models and gateways still need to support OpenAI-compatible audio input; users
can switch off direct delivery to use the transcription path instead.

## Verification

```bash
npm ci --ignore-scripts --prefix pi_runtime
python test_pi_runtime.py
python test_smoke.py
```
