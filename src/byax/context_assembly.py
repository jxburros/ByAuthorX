"""Token budgets for assembled AI requests (context engine plan 5.5).

Phase 2 (cache-ordered layout) and Phase 4 (router, inspector) extend this module. Today it
owns three things: a token estimate that never needs the network, budgets expressed in tokens
that keep the character limits in ``providers.py`` as hard safety caps, and the Ollama
``num_ctx`` plan, which refuses a request that cannot fit instead of letting the server
truncate it silently.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass

from .errors import ByaxError

# No provider tokenizer is bundled, and nothing is downloaded to get one. Without a registered
# tokenizer the estimate is conservative: English prose runs about four characters per token
# and code or numbers fewer, so 3.5 over-counts rather than under-counts. Characters outside
# the Latin and symbol ranges (CJK, Hangul, kana) are counted as one token each.
CHARS_PER_TOKEN = 3.5
WIDE_CODEPOINT = 0x2E80
# Fixed cost per chat message for role markers and separators.
MESSAGE_OVERHEAD_TOKENS = 4
# Room kept free for the model's answer. Shrinks when the window is small.
RESPONSE_HEADROOM_TOKENS = 1024
MIN_RESPONSE_HEADROOM_TOKENS = 128
# Default ceiling for Ollama's num_ctx. Ollama allocates memory in proportion to num_ctx, so this
# is a per-provider setting (``max_context_tokens``) rather than the model's maximum.
DEFAULT_MAX_CONTEXT_TOKENS = 8192
NUM_CTX_STEP = 256
# The library passage budget that existed before token budgets, in characters (retrieval.py).
LIBRARY_PASSAGE_CHARS = 12000

# kind -> callable(text) -> number of tokens. Offline tokenizers register here; none is bundled.
_TOKENIZERS: dict = {}


def register_tokenizer(kind, tokenizer):
    """Install an offline tokenizer for a provider kind (``None`` removes it)."""
    if tokenizer is None:
        _TOKENIZERS.pop(kind, None)
    else:
        _TOKENIZERS[kind] = tokenizer


def _kind(provider):
    if provider is None:
        return None
    if isinstance(provider, str):
        return provider
    if isinstance(provider, dict):
        return provider.get('kind')
    return getattr(provider, 'kind', None)


def estimate_tokens(text, provider=None):
    """Tokens ``text`` will cost: the provider's offline tokenizer if registered, else a safe ratio.

    ``provider`` may be a ProviderConfig, a provider dict, a kind string or None.
    """
    if not text:
        return 0
    tokenizer = _TOKENIZERS.get(_kind(provider))
    if tokenizer is not None:
        # A broken tokenizer must fall back to the safe estimate, never fail the request.
        with contextlib.suppress(Exception):
            return max(0, int(tokenizer(text)))
    wide = sum(1 for char in text if ord(char) >= WIDE_CODEPOINT)
    return math.ceil((len(text) - wide) / CHARS_PER_TOKEN + wide)


def chars_to_tokens(chars):
    return math.ceil(chars / CHARS_PER_TOKEN)


def tokens_to_chars(tokens):
    return int(tokens * CHARS_PER_TOKEN)


@dataclass(frozen=True)
class TokenBudget:
    """A budget in tokens plus the character limit that stays as a hard cap."""

    tokens: int
    chars: int

    def fits(self, text, *, used_tokens=0, used_chars=0, provider=None):
        return (
            used_chars + len(text) <= self.chars
            and used_tokens + estimate_tokens(text, provider) <= self.tokens
        )

    def report(self, *, used_tokens=0, used_chars=0):
        return {
            'unit': 'tokens',
            'maximum': self.tokens,
            'used': used_tokens,
            'estimate': 'conservative'
            if not _TOKENIZERS
            else 'provider tokenizer where registered, conservative otherwise',
            'character_cap': self.chars,
            'characters_used': used_chars,
        }


def budget_from_chars(max_chars):
    """The token budget equivalent to an existing character budget; the characters remain a cap."""
    return TokenBudget(tokens=chars_to_tokens(max_chars), chars=max_chars)


def response_headroom(window):
    return min(RESPONSE_HEADROOM_TOKENS, max(MIN_RESPONSE_HEADROOM_TOKENS, window // 4))


def context_window(config):
    """Tokens the provider can take for one request, or None when the window is not known.

    Ollama: the lower of the per-provider cap and the model's real context when known. Other
    providers declare no window here; their character limit (``max_input_chars``) is the cap.
    """
    if config.kind != 'ollama':
        return None
    cap = getattr(config, 'max_context_tokens', None) or DEFAULT_MAX_CONTEXT_TOKENS
    real = getattr(config, 'context_tokens', None)
    return min(cap, real) if real else cap


def input_budget(config):
    """Tokens available for the assembled request (window minus response headroom)."""
    window = context_window(config)
    if window is None:
        return budget_from_chars(config.max_input_chars)
    return TokenBudget(tokens=max(0, window - response_headroom(window)), chars=config.max_input_chars)


def library_budget(config, max_chars=LIBRARY_PASSAGE_CHARS):
    """Token budget for retrieved library passages.

    The old 12,000 character limit, converted, and never more than half of the request budget so
    that the document text and the question keep room. The character limit stays a hard cap.
    """
    base = budget_from_chars(max_chars)
    available = input_budget(config).tokens
    return TokenBudget(tokens=min(base.tokens, available // 2), chars=max_chars)


def payload_tokens(instruction, messages, provider=None):
    """Estimated tokens for a system instruction plus chat messages (``role``/``content``)."""
    return estimate_tokens(instruction, provider) + sum(
        estimate_tokens(message.get('content', ''), provider) + MESSAGE_OVERHEAD_TOKENS
        for message in messages
    )


def ollama_context_plan(config, instruction, messages, *, enforce=True):
    """``num_ctx`` for one Ollama request, or ``limit_exceeded`` before anything is sent.

    ``num_ctx`` is the assembled payload plus response headroom, rounded up, and never above the
    window (the provider's ``max_context_tokens`` or the model's real context, whichever is
    lower). Ollama truncates an over-long prompt without saying so; this refuses instead.
    """
    window = context_window(config)
    needed = payload_tokens(instruction, messages, config)
    headroom = response_headroom(window)
    total = needed + headroom
    plan = {
        'payload_tokens': needed,
        'response_headroom_tokens': headroom,
        'tokens_needed': total,
        'tokens_available': window,
        'num_ctx': min(window, math.ceil(total / NUM_CTX_STEP) * NUM_CTX_STEP),
        'fits': total <= window,
        'estimate': 'conservative characters-per-token ratio unless a provider tokenizer is registered',
    }
    if enforce and not plan['fits']:
        real = getattr(config, 'context_tokens', None)
        raise ByaxError(
            'limit_exceeded',
            f'{total} tokens needed / {window} available',
            tokens_needed=total,
            tokens_available=window,
            payload_tokens=needed,
            response_headroom_tokens=headroom,
            model_context_tokens=real,
            guidance='Select less text or fewer library passages'
            + (
                ', or raise max_context_tokens for this provider (the model reports ' + str(real) + ')'
                if real and real > window
                else ', or raise max_context_tokens if the model supports a larger window (Test connection reads it)'
                if not real
                else ''
            ),
        )
    return plan
