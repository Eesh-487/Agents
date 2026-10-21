import os
import re
import time
from types import SimpleNamespace

from dotenv import load_dotenv
from openai import RateLimitError

from jobs import JobCancelled

load_dotenv()


GENERATOR_MODEL = os.getenv("GENERATOR_MODEL", "mlx-community/Qwen3-8B-4bit")
DISCRIMINATOR_MODEL = os.getenv("DISCRIMINATOR_MODEL", "mlx-community/Qwen3-8B-4bit")


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_REASONING_EFFORTS = {"medium", "high"}

_REASONING_BUDGET = int(os.getenv("REASONING_BUDGET_TOKENS", "1024"))


def _strip_thinking(text):
    """Qwen3 emits its chain of thought inside <think>...</think> ahead of the
    real answer. Every caller here feeds the result straight into a JSON parser
    or a report, so the block has to go. An unterminated block means max_tokens
    cut the model off mid-thought - there is no answer left to keep."""
    text = _THINK_RE.sub("", text)
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


# Hosted OpenAI-compatible endpoint (OpenAI, Groq, OpenRouter, Ollama, ...)
# set from the frontend via PUT /llm/config. While set, every call goes there
# instead of local MLX, and the per-call MLX model names are ignored in favour
# of the one configured model. Held in memory only - the API key is never
# written to disk, so a backend restart falls back to local MLX.
# ponytail: one process-wide setting, fine for this single-user app; make it
# per-job if several users ever share one backend.
_remote = None


def configure_remote(base_url, api_key, model):
    """Point all LLM calls at a hosted endpoint, or back to local MLX when
    `model` is empty. A blank `api_key` keeps the current key, so the frontend
    can edit the URL or model without ever reading the key back."""
    global _remote
    if not model:
        _remote = None
        return
    if not api_key and _remote is not None:
        api_key = _remote.api_key
    from openai import OpenAI

    # Local servers like Ollama need no key, but the client refuses an empty one.
    client = OpenAI(base_url=base_url or None, api_key=api_key or "not-needed")
    _remote = SimpleNamespace(client=client, base_url=base_url, model=model, api_key=api_key,
                              has_key=bool(api_key))


# Hosts without Apple Silicon (any cloud deploy) can't run MLX, and the
# PUT /llm/config setting above is lost on every restart - so the hosted
# endpoint can also be set at startup from env. The frontend can still
# override it at runtime.
if os.getenv("LLM_MODEL"):
    configure_remote(os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL"))


def remote_config():
    if _remote is None:
        return {"provider": "local", "model": GENERATOR_MODEL}
    return {
        "provider": "remote",
        "base_url": _remote.base_url,
        "model": _remote.model,
        "has_api_key": _remote.has_key,
    }


def _create_remote(messages, temperature, max_tokens, top_p, frequency_penalty):
    remote = _remote
    completion = remote.client.chat.completions.create(
        model=remote.model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        # Sent only when non-default: several OpenAI-compatible providers
        # reject these outright rather than ignoring them.
        **({"top_p": top_p} if top_p != 1 else {}),
        **({"frequency_penalty": frequency_penalty} if frequency_penalty else {}),
    )
    choice = completion.choices[0]
    if choice.finish_reason == "length":
        print(f"[llm] {remote.model} hit the {max_tokens}-token cap - the response is TRUNCATED.")
    # Hosted reasoning models (Qwen3, DeepSeek-R1) can emit <think> too.
    message = SimpleNamespace(role="assistant", content=_strip_thinking(choice.message.content or ""))
    return SimpleNamespace(
        choices=[SimpleNamespace(index=0, message=message, finish_reason=choice.finish_reason)]
    )


_MODEL_CACHE = {}

_MODEL_CACHE_SIZE = int(os.getenv("MODEL_CACHE_SIZE", "1"))

# Off by default: measured on an M4/16GB at ~2k-token prompts, 8-bit KV cost
# 40% of throughput (9.7 vs 16.1 tok/s) and saved nothing - peak GPU memory was
# 5.33GB either way, because a cache that small is not what fills memory. Set
# KV_BITS=8 only for genuinely long contexts, where the cache does dominate.
_KV_BITS = int(os.getenv("KV_BITS", "0")) or None

_QUANTIZED_KV_START = int(os.getenv("QUANTIZED_KV_START", "0"))

_DRAFT_MODEL = os.getenv("DRAFT_MODEL", "mlx-community/Qwen3-0.6B-4bit")

_NUM_DRAFT_TOKENS = int(os.getenv("NUM_DRAFT_TOKENS", "3"))

# How many sequences decode concurrently in a batched call. Each one carries
# its own KV cache, so this is the knob that trades memory for throughput -
# lower it first if a batched stage hits a Metal "Insufficient Memory" abort.
_BATCH_SIZE = int(os.getenv("BATCH_SIZE", "8"))


def _load_model(model_name):
    """Keep `_MODEL_CACHE_SIZE` models resident, evicting oldest-first.

    Default is 1: the two tiers are ~4.3GB + ~7.8GB at 4-bit, and this process
    also holds torch, the sentence-transformers embedder and the cross-encoder
    from `retrieval.py`. That does not fit in 16GB of unified memory, and a
    model that swaps is slower than one that reloads. Raise MODEL_CACHE_SIZE to
    2 on a 32GB+ box - the tiers alternate constantly, so keeping both resident
    is a large win wherever the memory is actually there.

    MLX frees into its own pool rather than back to the OS, so the eviction has
    to call `clear_cache()` or the freed weights never become available again."""
    if model_name not in _MODEL_CACHE:
        import mlx.core as mx
        from mlx_lm import load

        while len(_MODEL_CACHE) >= _MODEL_CACHE_SIZE:
            _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)))
            mx.clear_cache()
        _MODEL_CACHE[model_name] = load(model_name)
    return _MODEL_CACHE[model_name]


_DRAFT_CACHE = {}


def _load_draft(target_model_name):
    """Draft model for speculative decoding, or None when it is switched off.

    Speculative decoding is exact, not an approximation: the draft proposes
    tokens, the target scores them in one batched pass, and a rejection step
    keeps only what the target itself would have sampled. Output distribution
    is unchanged - it buys speed, never quality. Set DRAFT_MODEL="" to disable
    (worth measuring: on high-entropy output a low acceptance rate makes this
    slower, since you pay for drafting *and* verification).

    Deliberately NOT in _MODEL_CACHE: that cache evicts down to
    MODEL_CACHE_SIZE, and evicting the draft on every tier switch would defeat
    the point. It is ~335MB at 4-bit, small enough to just keep."""
    if not _DRAFT_MODEL or _DRAFT_MODEL == target_model_name:
        return None
    if _DRAFT_MODEL not in _DRAFT_CACHE:
        from mlx_lm import load

        _DRAFT_CACHE[_DRAFT_MODEL] = load(_DRAFT_MODEL)[0]
    return _DRAFT_CACHE[_DRAFT_MODEL]


def _create(model, messages, temperature=0.7, max_tokens=1000, top_p=1,
            frequency_penalty=0, reasoning_effort=None, **_unused):
    """OpenAI-shaped chat.completions.create backed by MLX, so `chatbot` below
    keeps calling the same method with the same arguments while the weights run
    locally.

    Qwen3 is a hybrid model, so `reasoning_effort` maps onto its thinking mode:
    "medium"/"high" let it reason inside <think>...</think> first, anything else
    (including the wrapper's "low" default) skips straight to the answer. Only
    the judge tier should turn it on - thinking is what makes a verifier catch
    what a one-pass read misses, and it is dead weight on extraction and
    drafting, where the prompt already fixes the output shape.

    The trace is generated, used to condition the answer, then stripped: no
    caller can survive <think> arriving at its JSON parser. `_REASONING_BUDGET`
    is added on top of `max_tokens` so turning thinking on never eats into the
    answer length the caller asked for - the two budgets stay independent."""
    if _remote is not None:
        return _create_remote(messages, temperature, max_tokens, top_p, frequency_penalty)

    from mlx_lm.generate import stream_generate
    from mlx_lm.sample_utils import make_logits_processors, make_sampler

    mlx_model, tokenizer = _load_model(model)
    draft_model = _load_draft(model)
    thinking = str(reasoning_effort or "").lower() in _REASONING_EFFORTS
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, enable_thinking=thinking
    )
    # The KV cache, not the weights, is what tips a 16GB machine into a Metal
    # "Insufficient Memory" abort: the 14B's weights are a fixed ~7.8GB, but the
    # cache grows with prompt length + max_tokens, and the long-context callers
    # (GAP_MERGE sends the whole graph, the verifiers send the whole policy) are
    # exactly the ones also running a reasoning budget on top. 8-bit KV is very
    # close to lossless and roughly halves that growth. Set KV_BITS=0 to disable.
    # stream_generate rather than generate(): only the streaming form reports
    # finish_reason, and "length" (hit the cap) vs "stop" (model finished) is
    # the difference between a complete answer and a silently truncated one.
    # Truncated JSON does not fail loudly - json_utils repairs it by dropping
    # the incomplete tail, so the caller gets a well-formed, quietly incomplete
    # object. That has to be visible.
    token_budget = max_tokens + (_REASONING_BUDGET if thinking else 0)
    chunks, finish_reason = [], None
    try:
        for response in stream_generate(
            mlx_model,
            tokenizer,
            prompt=prompt,
            max_tokens=token_budget,
            draft_model=draft_model,
            # 512 not the 2048 default: at ~12k context a 2048-token prefill
            # chunk held one Metal command buffer long enough to trip the GPU
            # watchdog (kIOGPUCommandBufferCallbackErrorTimeout) on 16GB.
            prefill_step_size=512,
            sampler=make_sampler(temp=float(temperature), top_p=float(top_p)),
            logits_processors=(
                make_logits_processors(repetition_penalty=1.0 + float(frequency_penalty))
                if frequency_penalty
                else None
            ),
            **(
                {"kv_bits": _KV_BITS, "quantized_kv_start": _QUANTIZED_KV_START}
                if _KV_BITS
                else {}
            ),
            # Only valid on the speculative path - generate_step rejects it.
            **({"num_draft_tokens": _NUM_DRAFT_TOKENS} if draft_model else {}),
        ):
            chunks.append(response.text)
            finish_reason = response.finish_reason
        text = "".join(chunks)
    finally:
        # MLX frees into its own pool, so without this the cache from one call
        # is still held when the next one allocates. Runs on the failure path
        # too - an OOM must not leave the pool pinned for every later request.
        import mlx.core as mx

        mx.clear_cache()
    if finish_reason == "length":
        print(
            f"[llm] {model} hit the {token_budget}-token cap - the response is "
            f"TRUNCATED. Downstream JSON repair will silently drop the cut-off "
            f"tail; raise max_tokens at the call site or ask for less output."
        )
    content = _strip_thinking(text)
    if text.strip() and not content:
        # The model spent its whole budget inside <think> and never reached an
        # answer. Returning "" hands the caller an empty string that fails as
        # "Expecting value: line 1 column 1", which says nothing about the real
        # cause - say it here instead.
        print(
            f"[llm] {model} produced only reasoning within "
            f"{max_tokens + _REASONING_BUDGET} tokens and no answer - "
            f"raise REASONING_BUDGET_TOKENS or lower reasoning_effort."
        )
    message = SimpleNamespace(role="assistant", content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(index=0, message=message, finish_reason=finish_reason)]
    )


client = SimpleNamespace(
    chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
)


def generate_batch(system, prompts, model=GENERATOR_MODEL, temperature=0.7,
                   max_tokens=1000, top_p=1, cancel_event=None):
    """Run many INDEPENDENT prompts through the model in one batched pass.
    Returns a list of response strings, one per prompt, in the same order.

    Decoding is bound by the memory bandwidth needed to read the model's
    weights, and a single weight read serves every sequence in the batch - so
    N prompts batched cost far less than N prompts run one after another. The
    pipeline's slowest stages are loops over items that do not depend on each
    other (one call per graph entity, per law-section batch, per gap), which
    is exactly the shape this exists for.

    Deliberately NOT routed through `chatbot`: that class is a single-turn
    conversational wrapper around one prompt, and batching is a different
    shape. Anything needing conversation state or retry feedback should keep
    using `chatbot`; this is for fan-out.

    No speculative decoding here - mlx_lm's batch path does not take a draft
    model, and batching is the larger win of the two anyway.
    """
    if not prompts:
        return []
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled("Stopped before the next LLM call.")

    if _remote is not None:
        # No batch endpoint to share a weight read with - one call per prompt,
        # through chatbot so rate-limit retries still apply.
        # ponytail: sequential; a thread pool would cut wall-clock if provider limits allow.
        results = []
        for prompt in prompts:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("Stopped before the next LLM call.")
            agent = chatbot(system, model=model, temperature=temperature, max_tokens=max_tokens,
                            top_p=top_p, cancel_event=cancel_event)
            results.append(agent(prompt))
        return results

    from mlx_lm.generate import batch_generate
    from mlx_lm.sample_utils import make_sampler

    mlx_model, tokenizer = _load_model(model)

    def _encode(user_prompt):
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": user_prompt})
        return tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, enable_thinking=False
        )

    token_prompts = [_encode(prompt) for prompt in prompts]

    try:
        response = batch_generate(
            mlx_model,
            tokenizer,
            prompts=token_prompts,
            max_tokens=max_tokens,
            sampler=make_sampler(temp=float(temperature), top_p=float(top_p)),
            completion_batch_size=_BATCH_SIZE,
            **(
                {"kv_bits": _KV_BITS, "quantized_kv_start": _QUANTIZED_KV_START}
                if _KV_BITS
                else {}
            ),
        )
    finally:
        import mlx.core as mx

        mx.clear_cache()

    print(
        f"[llm] batched {len(prompts)} prompts: "
        f"{response.stats.generation_tokens} tokens at "
        f"{response.stats.generation_tps:.1f} tok/s"
    )
    return [_strip_thinking(text) for text in response.texts]


_RETRY_AFTER_RE = re.compile(r"try again in (?:([\d.]+)h)?(?:([\d.]+)m)?(?:([\d.]+)s)?", re.IGNORECASE)


def _seconds_until_free(error, default=5.0):
    """Groq's 429 already says exactly how long until the limit clears -
    either the Retry-After header or embedded in the error message ("...
    Please try again in 1.2s" or "...1h3m37.15s" for daily limits). Wait
    that long instead of guessing with a fixed backoff or failing outright."""
    response = getattr(error, "response", None)
    header_value = response.headers.get("retry-after") if response is not None else None
    if header_value:
        try:
            return float(header_value)
        except ValueError:
            pass
    match = _RETRY_AFTER_RE.search(str(error))
    if not match:
        return default
    hours, minutes, seconds = match.groups()
    total = (float(hours or 0) * 3600) + (float(minutes or 0) * 60) + float(seconds or 0)
    return total if total > 0 else default

class chatbot:
    def __init__(self, system, model=GENERATOR_MODEL, temperature=0.7, max_tokens=1000 , top_p=1, frequency_penalty=0, reasoning_effort="low", cancel_event=None):
        self.system = system
        self.model=model
        self.temperature= temperature
        self.max_tokens= max_tokens
        self.top_p= top_p
        self.frequency_penalty= frequency_penalty
        self.reasoning_effort = reasoning_effort
        self.cancel_event = cancel_event
        self.messages = []
        if self.system:
                    self.messages = [{"role": "system", "content": self.system}]
    def __messages_append__(self, role , content):
        self.messages.append({"role": role, "content": content})

    def __execute__(self,user):
          while True:
              try:
                  completion = client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=self.top_p,
                    frequency_penalty=self.frequency_penalty,
                    reasoning_effort=self.reasoning_effort)
                  return completion.choices[0].message.content
              except RateLimitError as error:
                  wait_s = _seconds_until_free(error)
                  print(f"[llm] {self.model} rate limited, waiting {wait_s:.1f}s before retrying...")
                  if self.cancel_event is not None:
                      if self.cancel_event.wait(timeout=wait_s):
                          raise JobCancelled("Stopped while waiting for a rate limit to clear.")
                  else:
                      time.sleep(wait_s)

    def __call__(self,message,role="user"):
        self.__messages_append__(role,message)
        result =  self.__execute__(message)
        self.__messages_append__("assistant",result)
        return result


if __name__ == "__main__":
    assert _strip_thinking("<think>step one\nstep two</think>answer") == "answer"
    assert _strip_thinking("plain answer, no thinking") == "plain answer, no thinking"
    assert _strip_thinking("before<think>mid</think>after") == "beforeafter"
    assert _strip_thinking("<think>cut off before closing tag") == ""
    print("llm self-check OK")
