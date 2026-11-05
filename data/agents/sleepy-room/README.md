# Moss: NPC configuration

This folder follows the supplied multi-document agent template. Every NPC has its own files; no source-template personal data is copied.

- IDENTITY.md: character identity.
- SOUL.md: personality and temperament.
- AGENTS.md: operating rules and behavioral constraints.
- USER.md: fictional operator/assignment expectations, not a real user profile.
- MEMORY.md: manually authored background, not automatic episode memory.
- ALIGNMENT_SYNTHESIS.md: reconcile temperament, objectives and world rules.
- PROACTIVE_PREFERENCES.md: initiative and planning preferences.
- people/INDEX.md and optional people/*.md: fictional background relationships.

Machine settings remain in ../sleepy-room.json. Only that JSON and the world define enforceable capabilities and carrying limits. Markdown cannot grant powers.

Markdown is loaded when profiles are listed or a new adventure starts and is frozen into that adventure. Editing files does not modify existing adventures. All documents except this README enter the owning NPC's persona context; no other NPC's files are loaded. Keep background free of hidden scenario solutions.

Missing standard files are recreated without overwriting existing ones. Edit Markdown directly or use the admin document editor. Legacy JSON persona edits update their untouched default Markdown counterpart; customized counterparts must be edited through Markdown instead. Clearing a document leaves an empty file. Removing a document from an API payload does not delete it from disk.
