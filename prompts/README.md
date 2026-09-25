# prompts/

**Specification artefacts, not runtime inputs.** Nothing in `backend/src`, `packages/` or
`frontend/src` loads these files: no agent reads a template from here, and no LLM is called with
one (`src/agents/llm_factory.py` has no callers; `llm-routing-no-callers` in
`conformance/declared_not_implemented.json`). They record what each skill's prompt is meant to
contain, in the four-part structure `docs/AGENTS.md` §6.3 specifies. A reference to one of these paths
elsewhere (for example a provenance record's `prompt_template_id` in a test) names the template; it
does not mean the file was read.

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
