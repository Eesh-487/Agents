import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

from jobs import JobCancelled

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
)

# Single source of truth for the two model tiers - chatbot's default (below)
# covers the generator tier, this covers the discriminator/judge tier. Import
# this rather than each agent file redefining its own `os.getenv(...)` line.
# Both tiers now route through OpenRouter to stealth/ox-alpha (a free,
# unlimited-during-preview stealth model) instead of Groq - these fallbacks
# match .env. NOTE: this is a temporary promo-window model (~1 week from its
# 2026-08-20 debut per OpenRouter) - if/when it's pulled or starts billing,
# this'll need a real replacement model, same as the Groq deprecation.
DISCRIMINATOR_MODEL = os.getenv("DISCRIMINATOR_MODEL", "stealth/ox-alpha")

# Groq formats short (per-minute) waits as plain seconds ("try again in
# 1.2s") but daily (TPD) waits as compound "1h3m37.15s" - the old pattern
# only matched the bare-seconds form, so a TPD-scale rate limit (the kind
# that actually recurs in this project) silently failed to match, fell back
# to the 5s default, and hammered the rate-limited endpoint every 5 seconds
# for the entire real wait instead of backing off correctly. All three
# groups are optional so both formats match the same pattern.
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
    def __init__(self, system, model=os.getenv("GENERATOR_MODEL", "stealth/ox-alpha"), temperature=0.7, max_tokens=1000 , top_p=1, frequency_penalty=0, reasoning_effort="low", cancel_event=None):
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
                  # reasoning_effort="low": the gpt-oss models (Groq's forced
                  # replacement for the deprecated llama models) spend their
                  # max_tokens budget on invisible reasoning tokens before the
                  # visible answer - at the default effort level, observed
                  # eating 3700-4000 of a 4000 max_tokens budget and returning
                  # an EMPTY completion (finish_reason="length") for every
                  # real extraction prompt. "low" drops that to ~300 and
                  # leaves the rest for the actual JSON output.
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
                  # A TPD-scale wait can be over an hour - call_agent_for_json
                  # only checks cancel_event *before* dispatching a call, so
                  # without this, a Stop request during a long rate-limit
                  # wait was previously invisible to this loop and the job
                  # would run until Groq's quota reset regardless. Event.wait
                  # (vs. time.sleep) returns immediately once cancelled
                  # instead of only being noticed on the next loop iteration.
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


