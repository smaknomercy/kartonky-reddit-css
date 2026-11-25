"""
LLM labelling of posters and Reddit comments with the scheme of prompts/annotation_guide.md (H4).

The model sees what the human annotators see: source, context line (city and date for
posters; subreddit and thread title for comments) and the text. One unit per call, so
labels do not depend on each other. The answer is forced into the same fields and
values as the annotation spreadsheets by a JSON schema.

Scopes
  sample : the 600 units of the manual annotation sample (annotation_v1), for validation
  all    : every unique poster text + every comment of the LLM pool (comment_pool())

Backends
  cli   : `claude -p` (Claude Code with subscription login, no API key)
  api   : Anthropic Messages API, one call per unit (ANTHROPIC_API_KEY in the environment or in .env)
  batch : Message Batches API, the same request per unit at half the price; results within 24 h.
          Two steps: submit (writes batches.json next to labels.jsonl), then collect (appends to
          labels.jsonl in the same format as api). Without --yes, submit only prints the plan and
          the cost estimate. Units in a batch that is not collected yet are not submitted again.

Output: data/labels/llm_v1/<scope>_<model>/
  labels.jsonl       one line per call: unit_id, labels, usage, error
  system_prompt.md   exact system prompt; schema.json  exact schema (methodology)
Re-running resumes: units already labelled without error under the same prompt and
schema are skipped; failed ones are retried.

Usage
  python scripts/labeling/llm_label.py --scope sample --model claude-haiku-5-5 --dry-run
  python scripts/labeling/llm_label.py --scope sample --model claude-haiku-5-5 --limit 5
  python scripts/labeling/llm_label.py --scope sample --model claude-sonnet-5-5 --workers 4
  python scripts/labeling/llm_label.py --scope all --model claude-haiku-5-5 --backend batch --limit 2000
  python scripts/labeling/llm_label.py --scope all --model claude-haiku-5-5 --backend batch --limit 2000 --yes
  python scripts/labeling/llm_label.py --scope all --model claude-haiku-5-5 --backend batch --collect
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_annotation_sample import ANNOTATORS, CLEAN, LABELS, OUT as ANN_DIR, comment_pool  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "prompts" / "annotation_guide.md"
OUT_ROOT = ROOT / "data" / "labels" / "llm_v1"

# Guide sections the model gets; the rest is logistics for the human annotators
GUIDE_SECTIONS = ["Що ви бачите", "Поля розмітки", "Приклади повністю"]

INTRO = """Ти розмічаєш тексти для дослідження протестів в Україні у липні–серпні 2026 року \
після звільнення Михайла Федорова з посади міністра оборони.

Кожен запит містить одну одиницю: джерело (poster — текст плаката з протесту, comment — \
коментар на Reddit), контекст і текст. Розміч її за інструкцією нижче. Це та сама \
інструкція, за якою розмічають люди; звертання «ви» в ній стосується і тебе.

Відповідай лише структурованою відповіддю за схемою, у ній є всі поля:
- target_*, irony, unsure: 1 = так, 0 = ні.
- Якщо on_topic = no, решту полів заповни так: усі target_* = 0, fedorov_stance = not_mentioned, \
speech_act = other, irony = 0, demand_main = none, demand_second = none, reference = none, \
sentiment = neutral. Вони не враховуються.
- Для плакатів: кожен плакат несли на протестах липня–серпня 2026, тож on_topic = yes, навіть \
якщо в тексті немає імен (емоційний вигук, гасло, вірш, мем). on_topic = no лише тоді, коли текст \
явно про інше (див. розділ «on_topic»).
- Для коментарів: якщо заголовок треду в контексті про нашу тему, on_topic = yes, навіть коли \
сам коментар тему не називає (див. правило про короткі репліки в розділі «Що ви бачите»).
- Якщо сумніваєшся, обери найкращий варіант, постав unsure = 1 і в comment назви поле й причину \
(до 12 слів). Якщо demand_main або demand_second = other, опиши вимогу в comment (до 12 слів). \
В інших випадках comment порожній.
"""


def build_system_prompt():
    guide = GUIDE.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", guide)
    keep = {p.split("\n", 1)[0].strip(): "## " + p.rstrip() for p in parts[1:]}
    missing = [s for s in GUIDE_SECTIONS if s not in keep]
    if missing:
        sys.exit(f"Guide sections not found: {missing}")
    return INTRO + "\n# Інструкція\n\n" + "\n\n".join(keep[s] for s in GUIDE_SECTIONS) + "\n"


def build_schema():
    props = {}
    for name, values in LABELS:
        if values is None:
            props[name] = {"type": "string"}
        elif values == "0,1":
            props[name] = {"type": "integer", "enum": [0, 1]}
        else:
            props[name] = {"type": "string", "enum": values.split(",")}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def user_message(u):
    return f"Джерело: {u['source']}\nКонтекст: {u['context']}\nТекст:\n{u['text']}"


def sample_units():
    """The 600 annotation units, with text and context exactly as the annotators see them."""
    frames = []
    for name in ANNOTATORS:
        for sheet in ("posters", "comments"):
            df = pd.read_excel(ANN_DIR / f"annotation_{name}.xlsx", sheet_name=sheet, dtype=str)
            frames.append(df[["unit_id", "source", "text", "context"]])
    u = pd.concat(frames).drop_duplicates("unit_id").fillna("")
    return u.sort_values("unit_id").reset_index(drop=True)


def all_units():
    """Unique poster texts (by text_norm) + the whole comment pool, formatted as in the sample."""
    k = pd.read_csv(CLEAN / "kartonky_clean.csv")
    k = k[k.has_text & k.text.notna()].drop_duplicates("text_norm")
    city = k.city_norm.fillna("місто невідоме")
    date = k.date_local.fillna("дата невідома") + \
        k.date_reliable.map({True: "", False: " (дата приблизна)"}).fillna("")
    posters = pd.DataFrame({"unit_id": "P_" + k.id.astype(str), "source": "poster",
                            "text": k.text, "context": city + ", " + date})
    c = comment_pool([])
    comments = pd.DataFrame({"unit_id": "C_" + c.id, "source": "comment",
                             "text": c.body, "context": "r/" + c.subreddit + " · " + c.link_title})
    return pd.concat([posters, comments], ignore_index=True).fillna("")


def call_cli(model, prompt_path, schema_json, msg, workdir, effort):
    # The unit goes through stdin: --tools and similar options may take several values,
    # so a positional prompt after them is not safe. --system-prompt-file replaces the
    # Claude Code prompt; --safe-mode skips CLAUDE.md, memory, skills, plugins and hooks.
    cmd = ["claude", "-p", "--model", model, "--system-prompt-file", str(prompt_path),
           "--tools", "", "--strict-mcp-config", "--safe-mode", "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "json", "--json-schema", schema_json]
    if effort:
        cmd += ["--effort", effort]
    # never let the CLI fall back to API credits: it must run on the subscription login
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    p = subprocess.run(cmd, input=msg, capture_output=True, text=True, timeout=300, cwd=workdir, env=env)
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    labels = res.get("structured_output")
    if res.get("is_error") or labels is None:
        raise RuntimeError(f"no structured output: {str(res.get('result'))[:300]}")
    meta = {k: res.get(k) for k in ("total_cost_usd", "usage", "modelUsage", "duration_ms", "num_turns")}
    return labels, meta


# USD per million tokens: input, output, cache read (synchronous API). Cache writes cost 1.25x input
# for the 5-minute cache and 2x for the 1-hour cache. The Batches API halves every price.
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01),
          "claude-sonnet-5-5": (2.00, 10.00, 0.10),
          "claude-opus-5-5": (4.00, 20.00, 0.20)}
BATCH_DISCOUNT = 0.5
BATCH_MAX_BYTES = 150 * 2**20  # the API takes up to 256 MB per batch; one request here is ~20 KB


def api_cost(model, u, batch=False):
    p = PRICES.get(model)
    if not p:
        return None
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens") or 0
    w5m = (u.get("cache_creation_input_tokens") or 0) - w1h
    usd = (u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1]
           + w5m * p[0] * 1.25 + w1h * p[0] * 2 + (u.get("cache_read_input_tokens") or 0) * p[2]) / 1e6
    return usd * (BATCH_DISCOUNT if batch else 1)


def api_params(model, system, schema, msg, ttl=None):
    cache = {"type": "ephemeral", **({"ttl": ttl} if ttl else {})}
    return dict(
        model=model, max_tokens=1024,
        system=[{"type": "text", "text": system, "cache_control": cache}],
        tools=[{"name": "record_labels", "description": "Record the labels for this unit.",
                "input_schema": schema}],
        tool_choice={"type": "tool", "name": "record_labels"},
        messages=[{"role": "user", "content": msg}],
    )


def parse_message(r, model, batch=False):
    block = next(b for b in r.content if b.type == "tool_use")
    usage = r.usage.model_dump()
    return block.input, {"usage": usage, "model": r.model, "total_cost_usd": api_cost(model, usage, batch)}


def call_api(client, model, system, schema, msg):
    return parse_message(client.messages.create(**api_params(model, system, schema, msg)), model)


def api_client():
    import anthropic
    env = ROOT / ".env"
    if "ANTHROPIC_API_KEY" not in os.environ and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
    return anthropic.Anthropic(max_retries=5)


# ---------- Message Batches API ----------

def load_batches(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_batches(path, batches):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(batches, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def estimate_cost(model, n, sample_dir):
    """Cost range for n units from the mean usage of the sample run: all cache hits vs none."""
    p, rows = PRICES.get(model), []
    f = sample_dir / "labels.jsonl"
    if p and f.exists():
        rows = [json.loads(line) for line in f.open(encoding="utf-8")]
        rows = [r["usage"] for r in rows if not r.get("error") and r.get("usage")]
    if not rows:
        return None
    m = lambda k: sum((r.get(k) or 0) for r in rows) / len(rows)  # noqa: E731
    prompt = m("input_tokens") + m("cache_read_input_tokens") + m("cache_creation_input_tokens")
    out, uncached = m("output_tokens"), m("input_tokens")
    best = (uncached * p[0] + (prompt - uncached) * p[2] + out * p[1]) / 1e6 * BATCH_DISCOUNT * n
    worst = (prompt * p[0] + out * p[1]) / 1e6 * BATCH_DISCOUNT * n
    return best, worst, out * p[1] / 1e6 * BATCH_DISCOUNT * n


def batch_submit(args, todo, system, schema, out, prompt_sha, schema_sha):
    state_path = out / "batches.json"
    batches = load_batches(state_path)
    pending = {uid for b in batches if not b.get("collected") for uid in b["units"].values()}
    todo = todo[~todo.unit_id.isin(pending)]
    if pending:
        print(f"{len(pending):,} units are in batches not collected yet: not submitted again")
    if not len(todo):
        print("nothing to submit")
        return
    # requests, split into chunks under the size limit; custom_id is a short index, mapped back to unit_id
    chunks, cur, size = [], [], 0
    for i, u in enumerate(todo.to_dict("records")):
        req = {"custom_id": f"u{i:06d}",
               "params": api_params(args.model, system, schema, user_message(u), ttl="1h")}
        b = len(json.dumps(req, ensure_ascii=False).encode())
        if cur and (size + b > BATCH_MAX_BYTES or len(cur) >= args.batch_size):
            chunks.append(cur)
            cur, size = [], 0
        cur.append((u["unit_id"], req))
        size += b
    chunks.append(cur)
    est = estimate_cost(args.model, len(todo), OUT_ROOT / f"sample_{args.model}")
    print(f"plan: {len(todo):,} units in {len(chunks)} batch(es) of up to {args.batch_size:,}")
    if est:
        print(f"estimated cost: ${est[0]:.2f} if the prompt is read from cache, ${est[1]:.2f} if never "
              f"(output alone ${est[2]:.2f}); batch caching is best-effort, so expect something in between")
    if not args.yes:
        print("dry run: add --yes to submit")
        return
    client = api_client()
    for ch in chunks:
        r = client.messages.batches.create(requests=[req for _, req in ch])
        batches.append({"id": r.id, "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "model": args.model, "prompt_sha": prompt_sha, "schema_sha": schema_sha,
                        "n": len(ch), "units": {req["custom_id"]: uid for uid, req in ch}, "collected": False})
        save_batches(state_path, batches)  # after every batch, so a crash never loses a batch id
        print(f"submitted {r.id}: {len(ch):,} requests")
    print(f"state in {state_path}; collect later with --collect (results are kept by the API for 29 days)")


def batch_collect(args, out):
    state_path, labels_path = out / "batches.json", out / "labels.jsonl"
    batches = load_batches(state_path)
    open_ = [b for b in batches if not b.get("collected")]
    if not open_:
        print("no batches to collect")
        return
    client = api_client()
    units = sample_units() if args.scope == "sample" else all_units()
    source = dict(zip(units.unit_id, units.source))
    for b in open_:
        r = client.messages.batches.retrieve(b["id"])
        c = r.request_counts
        print(f"{b['id']}: {r.processing_status} | processing {c.processing}, succeeded {c.succeeded}, "
              f"errored {c.errored}, canceled {c.canceled}, expired {c.expired}")
        if r.processing_status != "ended":
            continue
        n_ok, n_err, cost, rows = 0, 0, 0.0, []
        for e in client.messages.batches.results(b["id"]):
            uid = b["units"][e.custom_id]
            row = {"unit_id": uid, "source": source.get(uid, ""), "model": b["model"], "backend": "batch",
                   "prompt_sha": b["prompt_sha"], "schema_sha": b["schema_sha"], "batch_id": b["id"],
                   "labels": None, "error": None}
            if e.result.type == "succeeded":
                try:
                    labels, meta = parse_message(e.result.message, b["model"], batch=True)
                    row.update(labels=labels, **meta)
                    cost += meta["total_cost_usd"] or 0
                except StopIteration:
                    row["error"] = "no tool_use block in the answer"
            else:  # errored, canceled, expired: written as errors, so the next submit retries them
                row["error"] = f"batch {e.result.type}: {getattr(e.result, 'error', '')}"
            n_ok += row["error"] is None
            n_err += row["error"] is not None
            rows.append(row)
        with labels_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        b.update(collected=True, collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 ok=n_ok, errors=n_err, cost_usd=round(cost, 4))
        save_batches(state_path, batches)
        print(f"  collected: ok {n_ok:,}, errors {n_err:,}, cost ${cost:.3f} (${cost / max(n_ok, 1):.5f} per unit)")
    print(f"Saved to {labels_path}")


def load_done(path, prompt_sha, schema_sha):
    done = set()
    if path.exists():
        for line in path.open(encoding="utf-8"):
            r = json.loads(line)
            if not r.get("error") and r.get("prompt_sha") == prompt_sha and r.get("schema_sha") == schema_sha:
                done.add(r["unit_id"])
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["sample", "all"], default="sample")
    ap.add_argument("--model", required=True, help="full model id, e.g. claude-haiku-5-5")
    ap.add_argument("--backend", choices=["cli", "api", "batch"], default="api")
    ap.add_argument("--yes", action="store_true", help="batch: really submit (without it: plan and cost only)")
    ap.add_argument("--collect", action="store_true", help="batch: fetch finished batches into labels.jsonl")
    ap.add_argument("--batch-size", type=int, default=5000, help="batch: max requests per batch")
    ap.add_argument("--effort", choices=["low", "medium", "high"], help="cli only; default: model default")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, help="label only the first N pending units (smoke test)")
    ap.add_argument("--units", help="comma-separated unit_ids to label")
    ap.add_argument("--dry-run", action="store_true", help="write prompt and schema, show one message, no calls")
    args = ap.parse_args()

    system, schema = build_system_prompt(), build_schema()
    schema_json = json.dumps(schema, ensure_ascii=False)
    prompt_sha = hashlib.sha256(system.encode()).hexdigest()[:12]
    schema_sha = hashlib.sha256(schema_json.encode()).hexdigest()[:12]

    out = OUT_ROOT / f"{args.scope}_{args.model}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "system_prompt.md").write_text(system, encoding="utf-8")
    (out / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    labels_path = out / "labels.jsonl"

    if args.collect:
        return batch_collect(args, out)

    units = sample_units() if args.scope == "sample" else all_units()
    if args.units:
        units = units[units.unit_id.isin(args.units.split(","))]
    done = load_done(labels_path, prompt_sha, schema_sha)
    todo = units[~units.unit_id.isin(done)]
    if args.limit:
        todo = todo.head(args.limit)
    print(f"{args.scope}: {len(units):,} units, {len(done):,} done, {len(todo):,} to label "
          f"| {args.model} via {args.backend} | prompt {prompt_sha} ({len(system):,} chars), schema {schema_sha}")

    if args.dry_run:
        print("\n--- example message ---\n" + user_message(todo.iloc[0]) if len(todo) else "nothing to do")
        print(f"\nPrompt and schema written to {out}")
        return
    if not len(todo):
        return
    if args.backend == "batch":
        return batch_submit(args, todo, system, schema, out, prompt_sha, schema_sha)

    if args.backend == "api":
        client = api_client()
        label = lambda msg: call_api(client, args.model, system, schema, msg)  # noqa: E731
    else:
        workdir = tempfile.mkdtemp(prefix="llm_label_")  # empty cwd: no project files around
        prompt_path = out / "system_prompt.md"
        label = lambda msg: call_cli(args.model, prompt_path, schema_json, msg, workdir, args.effort)  # noqa: E731

    def run(u):
        t0, err, labels, meta = time.time(), None, None, {}
        for attempt in range(3):
            try:
                labels, meta = label(user_message(u))
                err = None
                break
            except Exception as e:  # keep going; failed units are retried on the next run
                err = f"{type(e).__name__}: {e}"
                if re.search(r"authenticat|401|login", err, re.I):
                    break  # retrying will not help: sign in again (claude auth login) and re-run
                time.sleep(5 * (attempt + 1))
        return {"unit_id": u["unit_id"], "source": u["source"], "model": args.model,
                "backend": args.backend, "prompt_sha": prompt_sha, "schema_sha": schema_sha,
                "labels": labels, "error": err, "seconds": round(time.time() - t0, 1), **meta}

    lock, n_ok, n_err, cost, start = threading.Lock(), 0, 0, 0.0, time.time()
    with labels_path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(run, u) for u in todo.to_dict("records")]
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            with lock:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
            n_ok += r["error"] is None
            n_err += r["error"] is not None
            cost += r.get("total_cost_usd") or 0
            if r["error"] and n_err <= 5:
                print(f"  {r['unit_id']}: {r['error']}")
            if i % 10 == 0 or i == len(futures):
                el = time.time() - start
                eta = el / i * (len(futures) - i)
                print(f"{i:,}/{len(futures):,} | ok {n_ok:,}, errors {n_err:,} | "
                      f"{el / 60:.1f} min, ETA {eta / 60:.1f} min | est. cost ${cost:.3f}", flush=True)
    print(f"Saved to {labels_path}")


if __name__ == "__main__":
    main()
