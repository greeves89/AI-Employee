"""Model pricing for cost estimation.

Duplicated from agent/app/llm_runner.py MODEL_PRICING so the orchestrator
can estimate costs without depending on the agent package.
"""

# Token pricing per 1M tokens (USD): (input_price, output_price)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-5": (1.25, 10.00),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Google
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}


def estimate_prompt_cost(prompt: str, model: str) -> dict:
    """Estimate the cost of running a prompt on a given model.

    Returns dict with min_usd, avg_usd, max_usd, estimated_input_tokens.
    Uses heuristic: ~4 chars per token for input, assumes 2000-token avg output.
    """
    # Estimate input tokens (prompt + system prompt overhead ~500 tokens)
    estimated_input = len(prompt) // 4 + 500

    # Lookup pricing
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        for known, prices in MODEL_PRICING.items():
            if model.startswith(known):
                pricing = prices
                break
    if not pricing:
        pricing = (3.00, 15.00)  # Default to Sonnet-tier pricing

    in_price, out_price = pricing

    # Estimate output: min 500 tokens, avg 2000, max 8000
    input_cost = (estimated_input / 1_000_000) * in_price
    min_cost = input_cost + (500 / 1_000_000) * out_price
    avg_cost = input_cost + (2000 / 1_000_000) * out_price
    max_cost = input_cost + (8000 / 1_000_000) * out_price

    return {
        "estimated_input_tokens": estimated_input,
        "model": model,
        "min_usd": round(min_cost, 6),
        "avg_usd": round(avg_cost, 6),
        "max_usd": round(max_cost, 6),
    }
