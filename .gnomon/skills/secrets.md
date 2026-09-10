+++
name = "secrets"
description = "Where API keys go, and what must never be written into the repository"
match = '\b(api[_-]?key|token|secret|credential|password|\.env|auth|login)\b'
+++

**Never write a secret into a file in the repository.** Not `.env`, not a config
file, not a comment, not a test fixture — regardless of `.gitignore`. A key in
the working tree is a key in backups, in editor state, in the next `tar`, and in
any transcript that pastes the file back.

Keys belong in the credential store, entered by the person who owns them:

    gnomon key set <endpoint>

That prompts, stores outside the surface, and never puts the value in the
surface hash. The surface names the VARIABLE (`api_key_env = "OPENCODE_API_KEY"`),
never the value.

**If a user pastes a key into the conversation**, do not write it anywhere. Say
plainly that it is now exposed and should be revoked and reissued, then tell them
the one command to store the replacement themselves.

**Authentication that needs a browser is not yours to do.** `gh auth login`,
`az login` and their equivalents are interactive. Report which command the user
should run and stop; that is a finished turn.
