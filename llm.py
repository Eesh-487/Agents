import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
)

# Single source of truth for the two model tiers - chatbot's default (below)
# covers the generator tier, this covers the discriminator/judge tier. Import
# this rather than each agent file redefining its own `os.getenv(...)` line.
DISCRIMINATOR_MODEL = os.getenv("DISCRIMINATOR_MODEL", "llama-3.3-70b-versatile")

_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)


def _seconds_until_free(error, default=5.0):
    """Groq's 429 already says exactly how long until the limit clears -
    either the Retry-After header or embedded in the error message ("...
    Please try again in 1.2s"). Wait that long instead of guessing with a
    fixed backoff or failing outright."""
    response = getattr(error, "response", None)
    header_value = response.headers.get("retry-after") if response is not None else None
    if header_value:
        try:
            return float(header_value)
        except ValueError:
            pass
    match = _RETRY_AFTER_RE.search(str(error))
    return float(match.group(1)) if match else default

class chatbot:
    def __init__(self, system, model=os.getenv("GENERATOR_MODEL", "llama-3.1-8b-instant"), temperature=0.7, max_tokens=1000 , top_p=1, frequency_penalty=0):
        self.system = system
        self.model=model
        self.temperature= temperature
        self.max_tokens= max_tokens
        self.top_p= top_p
        self.frequency_penalty= frequency_penalty
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
                    frequency_penalty=self.frequency_penalty)
                  return completion.choices[0].message.content
              except RateLimitError as error:
                  wait_s = _seconds_until_free(error)
                  print(f"[llm] {self.model} rate limited, waiting {wait_s:.1f}s before retrying...")
                  time.sleep(wait_s)

    def __call__(self,message,role="user"):
        self.__messages_append__(role,message)
        result =  self.__execute__(message)
        self.__messages_append__("assistant",result)
        return result


