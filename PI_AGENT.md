# Pi Coding Agent Runtime

The writing agent runs on the official `@earendil-works/pi-coding-agent`
package, version `0.81.1`. Python remains the authority for application
authentication, works, chapters, revisions, and the writing-specific database
tools.

## Native Pi Capabilities

- `pi_runtime/bridge.mjs` creates an official Pi Coding Agent session for each
  web turn.
- All native Pi tools are enabled: `read`, `bash`, `edit`, `write`, `grep`,
  `find`, and `ls`.
- Pi's normal project/global Skill discovery, `SKILL.md` invocation, extension
  loading, package loading, and project context files are enabled. Project
  resources are trusted intentionally for this agent runtime.
- The native tool working directory is `PI_AGENT_WORKSPACE_DIR` (the app
  project directory by default). Pi's normal global agent directory is still
  controlled by `PI_CODING_AGENT_DIR` or `~/.pi/agent`.
- `PI_AGENT_SKILL_DIR`, when configured, is added to Pi's normal Skill search
  paths; it does not replace Pi's other Skill locations.

## Writing Bridge

The existing writing tools are extra Pi custom tools. They keep the editor and
SQLite contracts intact while Pi remains free to use its native capabilities.
`activate_skill` and `read_skill_resource` only access Skills imported into the
application database; their private rule text is added for the current turn
and is not persisted in chat history.

The inspiration-library tools (`save_inspiration`, `search_inspirations`,
`get_inspiration`, `update_inspiration`, and `mark_inspiration_used`) use the
same bridge. Inspirations remain candidate creative material and never become
story facts merely because Pi retrieved them.

## Conversation Sessions

Each chapter or work scope can own multiple durable conversations. The active
session is passed to Pi only in standard mode. Ignore-history mode starts the
current model turn without earlier chat messages but appends the completed turn
to that durable session; temporary mode neither reads nor persists chat
messages.

Conversation compaction is based on the projected token cost of the complete
request, not a fixed character count. By default the runtime uses a 200,000
token window, triggers at 90 percent, reserves 8,192 tokens for output, and
summarizes only older messages while preserving recent tool-call boundaries.

## Local Launcher Lifecycle

The optional meta-memory launcher is separate from Pi's native Skills. It runs
only when both `AGENT_SKILL_DIR` and `AGENT_SKILL_LAUNCHER` are configured, and
then preserves its `before -> model -> after/recovery` protocol. Setting only a
Pi Skill directory leaves native Pi Skill loading active without attempting the
launcher lifecycle.

## Voice Input

Pi's public model contract represents text and images. For the existing
direct-voice feature, the bridge provides an OpenAI-compatible `input_audio`
adapter while retaining the same Pi Coding Agent session, native tools, and
loaded resources. The raw audio payload is never stored in conversation
history. It is copied to private inspiration storage only when Pi actually
calls `save_inspiration` during that voice turn.

## Verification

```bash
npm ci --ignore-scripts --prefix pi_runtime
.venv\Scripts\python.exe test_pi_runtime.py
.venv\Scripts\python.exe test_smoke.py
```
