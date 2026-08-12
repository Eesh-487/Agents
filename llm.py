import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
)

# Single source of truth for the two model tiers - chatbot's default (below)
# covers the generator tier, this covers the discriminator/judge tier. Import
# this rather than each agent file redefining its own `os.getenv(...)` line.
DISCRIMINATOR_MODEL = os.getenv("DISCRIMINATOR_MODEL", "llama-3.3-70b-versatile")

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
          completion = client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            top_p=self.top_p,
            frequency_penalty=self.frequency_penalty)
          return completion.choices[0].message.content

    def __call__(self,message,role="user"):
        self.__messages_append__(role,message)
        result =  self.__execute__(message)
        self.__messages_append__("assistant",result)
        return result


