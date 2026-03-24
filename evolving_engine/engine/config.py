"""Engine configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings


class EngineSettings(BaseSettings):
    """Configuration for the evolving engine."""

    # ---------------------------------------------------------------------------
    # Scope — what the engine can observe and modify
    # ---------------------------------------------------------------------------

    # Path to the Managed System's source code (read for scanning, write for changes)
    managed_app_path: Path = Path("../managed_app")

    # Path to the engine's own source code — enables self-modification
    self_path: Path = Path(".")

    # Root of the repository (used for full-repo scans and Git operations)
    repo_root: Path = Path("..")

    # Genesis — immutable initial state snapshot
    genesis_path: Path = Path("../genesis.yaml")

    # Purpose — the guiding specification for evolution decisions
    purpose_path: Path = Path("../purpose.yaml")

    # Purpose history — archived versions after Inception modifications
    purpose_history_path: Path = Path("../purpose_history")

    # Scratch space for staging generated files before validation
    workspace_path: Path = Path("/tmp/evolving_engine_workspace")

    # Temp root for sandbox copies/build contexts. In production this should
    # point at a tmpfs or other filesystem that is not the container overlay.
    sandbox_tmp_dir: Path = Path("/tmp")

    # Engine-maintained UTC daily usage ledger for proactive cost guardrails.
    usage_state_path: Path = Path("/tmp/evolving_engine_usage.json")

    # ---------------------------------------------------------------------------
    # Runtime monitoring (Monitor phase — "M" in MAPE-K)
    # ---------------------------------------------------------------------------

    # Base URL of the Managed System's backend API (via control-plane network)
    monitor_url: str = "http://localhost:8000"

    # How often the engine polls the Managed System in autonomous mode (seconds)
    monitor_interval_seconds: int = 60

    # Anomaly thresholds
    monitor_error_rate_threshold: float = 0.05    # 5%   → triggers evolution
    monitor_latency_threshold_ms: float = 800.0   # 800ms → triggers evolution
    monitor_db_latency_threshold_ms: float = 200.0

    # ---------------------------------------------------------------------------
    # LLM Provider
    # ---------------------------------------------------------------------------

    llm_provider: str = "anthropic"           # "anthropic" | "bedrock" | "openai"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    # Fast model for analysis/planning — cheaper, good enough for reasoning
    # Falls back to main model if not available on the API key
    anthropic_model_fast: str = "claude-sonnet-4-20250514"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.2"
    openai_model_fast: str = "gpt-5.2"
    bedrock_region: str = "us-east-1"
    bedrock_model_id: str = "global.anthropic.claude-sonnet-4-20250514-v1:0"

    # ---------------------------------------------------------------------------
    # Generation
    # ---------------------------------------------------------------------------

    max_retries: int = 3
    max_tokens: int = 64000

    # ---------------------------------------------------------------------------
    # Sandbox
    # ---------------------------------------------------------------------------

    sandbox_type: str = "docker"              # "docker" | "codebuild"
    sandbox_timeout_seconds: int = 300

    # ---------------------------------------------------------------------------
    # Autonomy guardrails (UTC daily budgets)
    # ---------------------------------------------------------------------------

    daily_llm_calls_limit: int = 60
    daily_input_tokens_limit: int = 500_000
    daily_output_tokens_limit: int = 120_000
    daily_proactive_runs_limit: int = 24
    daily_failed_evolutions_limit: int = 10
    daily_task_attempt_limit: int = 3

    # ---------------------------------------------------------------------------
    # Deployment (local — never pushes to GitHub)
    # ---------------------------------------------------------------------------

    # Path to the evolved application code (local git repo, never pushed)
    # This is separate from managed_app_path (the read-only template from GitHub).
    evolved_app_path: Path = Path("/opt/evolved-app")

    # Docker compose file used to rebuild managed system services after evolution
    compose_file: str = "docker-compose.prod.yml"

    # Root of the framework deployment (where compose file lives)
    deploy_root: Path = Path("/opt/self-evolving-software")

    # Docker compose project name — must match the running stack so the deployer
    # restarts the correct containers (not create duplicates).
    compose_project: str = "self-evolving-software"

    aws_region: str = "us-east-1"

    # ---------------------------------------------------------------------------
    # Observability
    # ---------------------------------------------------------------------------

    log_level: str = "INFO"

    model_config = {"env_prefix": "ENGINE_", "env_file": ".env", "extra": "ignore"}


settings = EngineSettings()
