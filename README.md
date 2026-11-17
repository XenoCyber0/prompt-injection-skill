# prompt-injection-generator

An agent **skill** that red-teams LLM apps for prompt injection: it maps the attack
surface, generates payloads per entry point and sink, runs them through a harness, and
scores what actually broke — with canary tokens so every "hit" is provable.

Works with any agent that reads `SKILL.md` skills (Claude Code, Codex, Cursor, etc.).

## What it does

1. **Recon** — finds entry points (chat, docs, RAG, tool output, JSON), sinks (answer,
   outbound HTTP, tool args, rendered markdown), tools, and guardrails.
2. **Matrix** — pairs a payload family with each (entry × sink) and assigns a canary +
   severity.
3. **Generate** — instantiate payloads from a catalog of 8 families (direct override,
   role hijack, indirect injection, exfiltration, tool/agent abuse, format hijack,
   encoding bypass, multi-turn) with real target names.
4. **Run** — `examples/run_harness.py` replays cases against a Python callable or HTTP
   endpoint; `examples/listener.py` catches exfil.
5. **Score & report** — `examples/score.py` returns Fail/Weak/Pass; the skill writes a
   remediation report and a regression suite.

## Install

```bash
# Claude Code (personal)
git clone https://github.com/<you>/prompt-injection-skill ~/.claude/skills/prompt-injection

# or copy into an existing skills dir
cp -r prompt-injection-skill ~/.agents/skills/
```

Then invoke it, e.g. *"red-team this chatbot for prompt injection"* or
`/prompt-injection-generator`.

## Layout

```
SKILL.md                 # the skill (agent instructions)
references/payloads.md   # payload catalog by family
references/encoding.md   # filter-bypass encodings
references/exfil.md      # canaries, listeners, side channels
references/harness.md    # harness + regression design
examples/run_harness.py  # drive cases (local callable or HTTP)
examples/score.py        # classify results
examples/listener.py     # canary listener
examples/cases.jsonl
```

## Quickstart

```bash
python examples/listener.py --port 8000 --out hits.log &
python examples/run_harness.py --cases examples/cases.jsonl --url http://localhost:3000/api/chat --limit 7 --delay 1
python examples/score.py results.jsonl
```

## Safety

Payloads are non-destructive by default. HTTP mode is capped with `--limit`; run against a
staging endpoint and a test account. Working payloads are the deliverable — raw responses
are logged to prove each one.

## License

MIT — see [LICENSE](LICENSE).
