# rag-on-aws — project brief for Claude Code

Read this file fully before any work. Follow the milestone order. Do one milestone per session unless told otherwise.

## What we are building

A production-style RAG system on AWS, deployable with one `terraform apply`, used as a public portfolio demo and as a reusable module for client work.

Flow: Markdown/text docs uploaded to S3 → ingest Lambda chunks + embeds them → vectors stored in S3 Vectors → `POST /ask` on API Gateway → query Lambda embeds the question, retrieves top-k chunks, generates a grounded answer via Bedrock Converse → returns answer + citations + latency + token usage.

## Fixed decisions (do not change without asking)

|Area|Decision|
|-|-|
|Region|`us-east-1` (widest Bedrock model access)|
|Vector store|Amazon S3 Vectors (`aws\_s3vectors\_vector\_bucket`, `aws\_s3vectors\_index`). Requires Terraform AWS provider **>= 6.24**|
|Pipeline|Own ingest/query Lambdas, NOT Bedrock managed Knowledge Bases|
|Embeddings|`amazon.titan-embed-text-v2:0`, 1024 dims, normalized, cosine distance|
|Generation|Bedrock Converse API; model ID is a Terraform variable, never hardcoded|
|Runtime|Python 3.12, boto3 only (no LangChain/LlamaIndex — keep it explainable)|
|API|API Gateway HTTP API, throttled (2 rps, burst 5), question max 500 chars, CORS for the demo UI|
|IaC|Terraform. Reusable module in `infra/modules/rag\_stack`, demo env in `infra/envs/demo`|
|CI|GitHub Actions: terraform fmt/validate, ruff, pytest. Deploy via GitHub OIDC role, no long-lived keys|
|Cost|AWS Budget with email alerts at 40/80/100% of `monthly\_budget\_usd` (default 20). Everything serverless; idle cost ≈ storage|

## Repo layout

```
app/common/   chunking, bedrock + s3vectors clients, config
app/ingest/   handler.py (S3 ObjectCreated → chunk → embed → put\_vectors)
app/query/    handler.py (embed → query\_vectors → converse)
evals/        questions.jsonl, run\_evals.py
infra/modules/rag\_stack/   main.tf, variables.tf, outputs.tf, iam.tf
infra/envs/demo/           main.tf, terraform.tfvars.example
web/          index.html (single static page)
scripts/      build\_lambdas.sh
tests/        pytest unit tests (mock AWS with moto/stubber where possible)
```

## Engineering rules

* Least-privilege IAM: each Lambda gets only the actions it uses, scoped to specific ARNs.
* S3: block public access, SSE encryption, versioning on docs bucket.
* Structured JSON logs from both Lambdas: request id, latency\_ms, input/output tokens, retrieved source keys + distances.
* Vector keys must be deterministic (`<s3\_key>#<chunk\_index>`) so re-uploading a doc overwrites instead of duplicating. Deleting a doc should delete its vectors.
* Store chunk text + source key + heading as vector metadata; mark the chunk text as non-filterable metadata.
* If the best retrieval distance is worse than a configurable threshold, answer "I don't know based on the provided documents" instead of guessing.
* The system prompt must instruct the model to answer only from the provided context and cite sources by \[n].
* Every milestone ends with: tests passing, `terraform fmt` clean, and a short summary of what changed and how to verify it.
* Explain non-obvious code with brief comments. The owner must be able to explain every component to a client.

## Milestones

Each milestone lists acceptance criteria. Stop after each one and report.

**M1 — Skeleton + infra base**

* Repo layout, `.gitignore`, ruff + pytest config, CI workflow (fmt/validate/lint/test).
* Terraform module: docs S3 bucket, S3 Vectors bucket + index, AWS Budget.

\- Done when: `terraform validate` succeeds offline (plan runs later, once AWS credentials exist); CI green.

**M2 — Ingest pipeline**

* Heading-aware chunker (size/overlap configurable, default 1000/150) with unit tests.
* Ingest Lambda + S3 event trigger + IAM. Batch embedding calls; handle create and delete events.
* `scripts/build\_lambdas.sh` produces zips.
* Done when: uploading docs to `docs/` produces vectors in the index (verify with a small CLI script).

**M3 — Query API**

* Query Lambda + HTTP API + throttling + IAM.
* Response JSON: `answer`, `sources\[{key, heading, distance}]`, `latency\_ms`, `usage{input\_tokens, output\_tokens}`.
* Distance threshold "I don't know" behaviour.
* Done when: `curl POST /ask` returns a cited answer; logs show the structured fields.

**M4 — Evals**

* `evals/run\_evals.py`: reads `questions.jsonl` (`question`, `expected\_sources`, `answerable`), calls the API or functions directly, reports hit@k, MRR, p50/p95 latency, avg tokens, correct-refusal rate. Writes a markdown results row.
* Owner writes the questions; Claude Code only provides the format and 3 examples.
* Done when: a baseline run produces a results table.

**M5 — Demo UI + hardening**

* `web/index.html`: question box, answer, clickable source list, latency/token badge. Hosted on S3 + CloudFront.
* IAM review, encryption check, OIDC deploy job in CI.
* Done when: public URL works; deploy runs from GitHub Actions.

**M6 — Docs polish**

* README: live URL, architecture diagram (mermaid), decisions table, final eval table, cost per 1,000 questions (calculated from logged tokens).

