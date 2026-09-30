"""Thin wrapper around the Anthropic Messages API.
- structured(): forces a single tool call -> returns validated JSON (our structured-output mechanism)
- chat(): one turn with tools, for agent loops
- disk cache keyed by request hash so a live demo doesn't re-pay for identical extraction calls
- every call is logged (model, tokens, latency) for the audit trail."""
import hashlib, json, time
from . import store
from .config import API_KEY, MODEL, CACHE_DIR

_client = None
_override = None   # tests can inject a fake client

class LLMUnavailable(RuntimeError):
    pass

def set_client(fake):
    global _override
    _override = fake

def client():
    global _client
    if _override is not None:
        return _override
    if _client is None:
        import os
        key = os.getenv("ANTHROPIC_API_KEY") or API_KEY
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example) and restart.")
        import anthropic
        _client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600)
    return _client

def available():
    import os
    return _override is not None or bool(os.getenv("ANTHROPIC_API_KEY") or API_KEY)

def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:24]

def _block_to_dict(b):
    if isinstance(b, dict):
        return b
    d = b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b)
    t = d.get("type")
    if t == "text":
        return {"type": "text", "text": d.get("text", "")}
    if t == "tool_use":
        return {"type": "tool_use", "id": d["id"], "name": d["name"], "input": d.get("input", {})}
    return d

def _friendly(e, model):
    s = str(e)
    low = s.lower()
    if "not_found" in low or "model" in low and ("not found" in low or "does not exist" in low or "invalid" in low):
        return f"Model '{model}' is not available on your API key. Add a line CLAUDE_MODEL=<a model from your console> to .env and restart. ({s[:200]})"
    if "credit" in low or "billing" in low or "balance" in low:
        return f"Your Anthropic account has no credit - add credit at console.anthropic.com > Billing. ({s[:200]})"
    if "authentication" in low or "api key" in low or "x-api-key" in low or "401" in low:
        return f"API key rejected - check the ANTHROPIC_API_KEY line in .env. ({s[:200]})"
    if "rate" in low and "limit" in low or "429" in low or "overloaded" in low or "529" in low:
        return f"Claude is rate-limited/overloaded right now - wait a minute and re-run. ({s[:200]})"
    return s[:600]

def _create(kw):
    """Streams long requests (avoids HTTP timeouts on big extractions); plain call otherwise."""
    c = client()
    if kw.get("max_tokens", 0) > 8000 and hasattr(c.messages, "stream"):
        with c.messages.stream(**kw) as st:
            return st.get_final_message()
    return c.messages.create(**kw)

def _call(**kw):
    t0 = time.time()
    try:
        try:
            resp = _create(kw)
        except Exception as e:
            # some newer models reject 'temperature' - retry once without it
            if "temperature" in str(e).lower() and "temperature" in kw:
                kw = {k: v for k, v in kw.items() if k != "temperature"}
                resp = _create(kw)
            else:
                raise
    except Exception as e:
        msg = _friendly(e, kw.get("model"))
        store.log("llm_error", {"model": kw.get("model"), "error": msg})
        print("LLM ERROR:", msg, flush=True)
        raise RuntimeError(msg) from e
    usage = getattr(resp, "usage", None)
    store.log("llm_call", {"model": kw.get("model"), "latency_s": round(time.time() - t0, 1),
                           "in_tokens": getattr(usage, "input_tokens", None), "out_tokens": getattr(usage, "output_tokens", None),
                           "stop": getattr(resp, "stop_reason", None)})
    return resp

def structured(system, content, tool_name, tool_description, schema, model=None, max_tokens=16000, cache_key=None, temperature=0):
    """Force the model to answer by calling `tool_name` with arguments matching `schema`. Returns dict."""
    model = model or MODEL
    req = {"model": model, "system": system, "content": content, "tool": tool_name, "schema": schema}
    key = cache_key or _hash(req)
    cf = CACHE_DIR / f"{tool_name}_{key}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    resp = _call(model=model, max_tokens=max_tokens, temperature=temperature, system=system,
                 messages=[{"role": "user", "content": content}],
                 tools=[{"name": tool_name, "description": tool_description, "input_schema": schema}],
                 tool_choice={"type": "tool", "name": tool_name})
    out = None
    for b in resp.content:
        if getattr(b, "type", None) == "tool_use" or (isinstance(b, dict) and b.get("type") == "tool_use"):
            out = b.input if hasattr(b, "input") else b["input"]
    if out is None:
        raise RuntimeError(f"Model did not return {tool_name} (stop_reason={getattr(resp,'stop_reason',None)})")
    if getattr(resp, "stop_reason", None) == "max_tokens":
        out["_truncated"] = True
    cf.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out

def chat(system, messages, tools=None, model=None, max_tokens=4000, temperature=0.2):
    """One assistant turn. Returns (content_blocks_as_dicts, stop_reason)."""
    kw = dict(model=model or MODEL, max_tokens=max_tokens, temperature=temperature, system=system, messages=messages)
    if tools:
        kw["tools"] = tools
    resp = _call(**kw)
    return [_block_to_dict(b) for b in resp.content], resp.stop_reason

def text(system, prompt, model=None, max_tokens=3000, temperature=0.3):
    blocks, _ = chat(system, [{"role": "user", "content": prompt}], model=model, max_tokens=max_tokens, temperature=temperature)
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
