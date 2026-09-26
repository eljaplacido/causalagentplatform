# prompts/

**One runtime template; the rest are specifications.** `explanation_generate.yaml` is loaded at
runtime by `backend/src/agents/prompt_templates.py` and rendered by `backend/src/agents/narration.py`
for the two callers of the LLM layer (the SimulationAgent's draft explanation, and
`POST /api/v1/decisions` with `narrate: true`). The loader refuses a repeated key, a missing part, a
`version` that is not `MAJOR.MINOR.PATCH`, a `skill` other than the file name, a
`provider_capability` the factory does not route, and `llm_required: false`, and rendering refuses
a missing or unexpected `{{variable}}`. The template's id (`prompts/<skill>.yaml@<version>`) and
the sha256 of the file are recorded on every LLM invocation made with it, so **editing a loaded
template changes what the model is asked: bump its `version`.** `PROMPTS_DIR` points the loader
elsewhere; the backend image copies this directory to `/app/prompts`.

The other thirteen files are not loaded: their skills call no model. They record what each
skill's prompt is meant to contain, in the four-part structure `docs/AGENTS.md` §6.3 specifies. A
reference to one of these paths elsewhere (for example a provenance record's `prompt_template_id`
in a test) names the template; it does not mean the file was read.

## Shape

Each file is a YAML mapping:

| Key | Holds |
|---|---|
| `version` | the template version |
| `skill` | the skill's name, equal to the file name |
| `provider_capability` | the capability the prompt would be routed by |
| `llm_required` | present and `false` for deterministic skills that need no model |
| `context` | the `[CONTEXT]` part |
| `skill_prompt` | the `[SKILL]` part: the instruction and its template variables (`{{...}}`) |
| `inputs` | typed inputs, for skills that document them instead of a `skill_prompt` |
| `constraints` | the `[CONSTRAINTS]` part |
| `output_format` | the `[OUTPUT_FORMAT]` part: the typed schema the answer must match |

`plan_action_sequence.yaml` and `provenance_trace.yaml` are written entirely as comments, so they
parse as empty documents.

## Checking them

Load with a loader that rejects duplicate keys. The standard `yaml.safe_load` keeps the *last*
duplicate silently: until the `[SKILL]` part was renamed to `skill_prompt`, ten of these files
declared `skill:` twice, and a plain load returned the instruction text as the skill's name.
