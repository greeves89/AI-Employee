from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    agent_id: str = "agent-001"
    agent_name: str = ""
    agent_token: str = ""
    redis_url: str = "redis://redis:6379"
    health_port: int = 8080
    default_model: str = "claude-sonnet-4-6"
    max_turns: int = 100
    extended_thinking: bool = False  # Not a CLI flag - thinking is model-controlled
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    workspace_dir: str = "/workspace"
    orchestrator_url: str = "http://orchestrator:8000"

    # Agent mode: "claude_code" (default CLI) or "custom_llm" (direct API)
    agent_mode: str = "claude_code"

    # Custom LLM settings (only used when agent_mode == "custom_llm")
    llm_provider_type: str = ""  # "openai" | "anthropic" | "google"
    llm_api_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = ""
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.7
    llm_system_prompt: str = ""
    llm_tools_enabled: bool = True

    # Custom MCP servers (JSON: {"name": "http://url"}) - used by both modes
    custom_mcp_servers: str = ""

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
