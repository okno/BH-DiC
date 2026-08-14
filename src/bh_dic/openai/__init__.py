"""Privacy-preserving OpenAI intent routing.

The model is deliberately limited to selecting a declared application intent.  It
never receives a browser, filesystem, HTTP, or execution tool.
"""

from bh_dic.openai.intent_router import MockIntentRouter, OpenAIIntentRouter
from bh_dic.openai.schemas import IntentEnvelope

__all__ = ["IntentEnvelope", "MockIntentRouter", "OpenAIIntentRouter"]
