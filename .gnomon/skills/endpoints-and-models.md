+++
name = "endpoints and model ids"
description = "How to point a role at a local or cloud endpoint, and how to get the model id right instead of guessing it"
match = '\b(endpoint|endpoints|model|models|opencode|openrouter|ollama|cloud|local|api[_-]?key|glm|gpt|claude|kimi|qwen|deepseek|route|routing)\b'
+++

**Never guess a model id, and never hand-edit one into roles.toml first.**

An endpoint publishes the ids it serves. Ask it:

    gnomon endpoint list     # every endpoint, grouped LOCAL vs CLOUD, and it
                             # cross-checks each role's model id against what
                             # that endpoint actually serves
    /models                  # inside the loop: lists them, and picks one for a role

`gnomon endpoint list` will say `✗ role plan names model "x" — this endpoint
does not serve it` and offer the nearest real id. That output is the answer.
Read it before changing anything.

**Model ids are bare.** `glm-5.3`, `deepseek-v4-flash`, `kimi-k3`. There is no
`opencode-go/` or other provider prefix, whatever a comment may say. A wrong id
comes back as a provider 400 that names the MODEL, which reads as "unavailable"
rather than "misspelled" — so a guess looks confirmed when it is not.

**Adding an endpoint is one command, not four file edits.**

    gnomon endpoint add --preset opencode-go | opencode-zen | openrouter | ollama

It asks the endpoint to run one token before writing anything, so a rejected key
or a bad model fails there rather than several turns into a task.

**Check whether the key is already there before asking for one.** `gnomon
endpoint list` prints `key: $VAR — set` when it is. A key can come from the
credential store and already be present; telling the user to run
`gnomon key set` when the listing says `set` is noise that hides the real fault.

**Local and cloud coexist.** A surface routinely runs volume roles on a local
endpoint and the hard ones on a cloud endpoint. Changing one role's endpoint
does not disturb the others. Set it per role:

    [roles.plan]
    model = "glm-5.3"      # an id the endpoint listed
    endpoint = "go"

**A surface change needs a restart.** `.gnomon/` is read when the process starts
and its hash is asserted every turn. Editing roles.toml mid-session does not
re-route the running session — say so, rather than reporting the change as live.
