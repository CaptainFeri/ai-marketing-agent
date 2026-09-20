# Running the agents

The contracts are in [`agent-contracts.md`](agent-contracts.md); this is how a
contract becomes a filled-in article.

```
advance_text                    queues the next step as a GPU job
  └─ GpuJob(llm_text)           waits for the card (handoff section 6)
       └─ gpu.dispatch          leases it, loads the text window
            └─ execute_step_job rebuilds the context from the database
                 └─ run_agent   prompts, validates, retries
                      └─ StepRun.output_json, then advance_text again
```

## The context is rebuilt, never carried

`build_context` reads the brand brief, the topic, the outputs of the stages
this one depends on, and any outstanding feedback — from the database, every
time. Nothing is threaded through the job payload.

That is what makes "re-run from any step" work (handoff phase 1, week 3): a
step re-run a week later sees exactly what it would have seen originally, plus
whatever feedback arrived since.

```bash
POST /api/v1/packages/{id}/steps/writer/rerun
```

Each stage is shown only what it needs. `DEPENDENCIES` in
`app/agents/context.py` says which earlier outputs reach it, and `BRIEF_FIELDS`
which parts of the brief. Handing the writer the keyword research as well as
the strategy doubles the prompt for nothing, and on a single card prompt length
is throughput.

## Prompts carry what a schema cannot

The output shape is already pinned by guided decoding, so the prompts spend no
tokens describing field names — that only invites the prose and the schema to
disagree. What they carry instead is the language, the brand's voice, the
evidence rule, and what this particular stage is for.

Temperature follows the job: 0.2 for QA, 0.7 for the writer and the marketizer.
Token budgets likewise — the writer gets 7000, QA gets 2500.

A few lines in `app/agents/prompts.py` are doing real work and should not be
trimmed:

- *"Do not translate from another language"* — decision D3. Persian keywords
  are researched in Persian; a translated English list is not the same thing.
- *"Never state a statistic … unless you have a source"* — section 13's main
  risk, restated for every agent because every agent can introduce one.
- *"Any text that must appear goes in overlay_text"* — FLUX renders Persian and
  Arabic script as nonsense, so type is drawn over the image afterwards.
- *"A generous score sends work to a human that should have gone back to the
  writer"* — the human's time is the scarce resource at that gate.

## Retries feed the errors back

`run_agent` validates every answer and, on failure, appends what was wrong to
the prompt and asks again — up to `AGENT_MAX_ATTEMPTS`. Retrying an identical
prompt against a model that already failed it usually fails the same way. The
retry also samples cooler, since the first sampling already produced something
malformed.

Two failure kinds, treated differently:

| | Meaning | Handling |
|---|---|---|
| `AgentOutputError` | The model answered, but not within the contract | Retried in-process with the errors attached; a final failure fails the step |
| `LlmError` | The model server could not be reached | Fails the job, which the queue retries later — nothing is wrong with the prompt |

## GPU time includes the attempts that failed

The quota ledger is charged `model_seconds`: the generation time summed over
every attempt, including rejected ones. They occupied the card too. Wall-clock
time is kept separately for diagnostics, because it also covers validation and
whatever else the worker was doing.

## Running without a model

`LLM_CLIENT=simulated` returns contract-valid output built by
`app/agents/simulation.py` and reports a plausible duration from the token
count, so the queue, the gates, the panel and the quota mechanism all behave as
they will in production. Every sample is tagged `[simulated]` — a simulated run
that looked like finished work would be worse than one that obviously is not.

`LLM_CLIENT=vllm` talks to a local vLLM server. The one setting worth checking
is `LLM_GUIDED_MODE`: recent vLLM takes the OpenAI-style `response_format`,
older builds want `guided_json`. Sending the wrong one means generation is not
constrained at all, which fails later and further away.

## What each stage's output does

| Stage | Effect |
|---|---|
| writer, geo_optimizer, seo_optimizer | Re-assemble `ContentPackage.article`; each returns the whole article, so the latest wins |
| qa | Score recorded; below threshold the package rewinds to the writer, at most `QA_MAX_RETRIES` times, then goes to a person |
| marketizer | Replaces the `Variant` rows — a re-run means the previous set was wrong, and leaving both would put two versions of one post in front of the editor |

`rewind_to` sets `current_step` to the *predecessor* of the target, because
`current_step` records the step that last ran. Assigning the target directly
would skip it — the opposite of what "send it back to the writer" means.

## Still to come

The agent prompts exist and the chain runs, but no article has been generated
by a real model yet: that is phase 0's acceptance criterion (7 of 10
publishable per language, per language). Expect the prompts to change once
there is output to read — they are written to be edited.
