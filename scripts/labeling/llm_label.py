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
  cli : `claude -p` (Claude Code with subscription login, no API key)
  api : Anthropic Messages API (ANTHROPIC_API_KEY in the environment or in .env)

Output: data/labels/llm_v1/<scope>_<model>/
  labels.jsonl       one line per call: unit_id, labels, usage, error
  system_prompt.md   exact system prompt; schema.json  exact schema (methodology)
Re-running resumes: units already labelled without error under the same prompt and
schema are skipped; failed ones are retried.

Usage
  python scripts/labeling/llm_label.py --scope sample --model claude-haiku-5-5 --dry-run
  python scripts/labeling/llm_label.py --scope sample --model claude-haiku-5-5 --limit 5
  python scripts/labeling/llm_label.py --scope sample --model claude-sonnet-5-5 --workers 4
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
    p = subprocess.run(cmd, input=msg, capture_output=True, text=True, timeout=300, cwd=workdir)
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    labels = res.get("structured_output")
    if res.get("is_error") or labels is None:
        raise RuntimeError(f"no structured output: {str(res.get('result'))[:300]}")
    meta = {k: res.get(k) for k in ("total_cost_usd", "usage", "modelUsage", "duration_ms", "num_turns")}
    return labels, meta


# USD per million tokens: input, output, cache write (5 min), cache read; synchronous API, no batch discount
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
          "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
          "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20)}


def api_cost(model, u):
    p = PRICES.get(model)
    if not p:
        return None
    return (u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1]
            + (u.get("cache_creation_input_tokens") or 0) * p[2]
            + (u.get("cache_read_input_tokens") or 0) * p[3]) / 1e6


def call_api(client, model, system, schema, msg):
    r = client.messages.create(
        model=model, max_tokens=1024,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=[{"name": "record_labels", "description": "Record the labels for this unit.",
                "input_schema": schema}],
        tool_choice={"type": "tool", "name": "record_labels"},
        messages=[{"role": "user", "content": msg}],
    )
    block = next(b for b in r.content if b.type == "tool_use")
    usage = r.usage.model_dump()
    return block.input, {"usage": usage, "model": r.model, "total_cost_usd": api_cost(model, usage)}


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
    ap.add_argument("--backend", choices=["cli", "api"], default="api")
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

    if args.backend == "api":
        import anthropic
        env = ROOT / ".env"
        if "ANTHROPIC_API_KEY" not in os.environ and env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
        client = anthropic.Anthropic(max_retries=5)
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
