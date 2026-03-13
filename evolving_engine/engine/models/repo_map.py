"""Models for the repository map — a JSON representation of the managed app's structure."""

from pydantic import BaseModel, Field


class FileNode(BaseModel):
    """A single file or directory in the repository tree."""

    path: str
    name: str
    is_dir: bool = False
    extension: str = ""
    size_bytes: int = 0
    children: list["FileNode"] = Field(default_factory=list)
    summary: str = ""  # Brief description of the file's purpose


class DBColumn(BaseModel):
    """A column within a database table."""

    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str | None = None  # e.g., "other_table.id"
    default: str | None = None


class DBTable(BaseModel):
    """A database table schema."""

    name: str
    columns: list[DBColumn] = Field(default_factory=list)


class DBSchema(BaseModel):
    """Complete database schema."""

    tables: list[DBTable] = Field(default_factory=list)


class Dependency(BaseModel):
    """A project dependency (Python package or npm package)."""

    name: str
    version: str = ""
    layer: str  # "frontend" | "backend" | "engine"


class APIEndpoint(BaseModel):
    """A registered API endpoint."""

    method: str  # GET, POST, PUT, DELETE
    path: str
    description: str = ""
    file_path: str = ""


class RepoMap(BaseModel):
    """Complete repository map — a token-efficient snapshot of the managed application.

    This is the primary context object that the Data Manager Agent builds and
    that other agents consume to understand the codebase without reading every file.
    """

    tree: FileNode | None = None
    db_schema: DBSchema = Field(default_factory=DBSchema)
    dependencies: list[Dependency] = Field(default_factory=list)
    api_endpoints: list[APIEndpoint] = Field(default_factory=list)
    react_components: list[str] = Field(default_factory=list)
    alembic_revisions: list[str] = Field(default_factory=list)
    summary: str = ""  # High-level description of the current state

    def to_context_string(self, max_chars: int = 8000) -> str:
        """Serialize the repo map into a concise string for LLM context injection.

        Truncates intelligently to stay within token budget.
        """
        parts = [f"# Repository Map\n\n{self.summary}\n"]

        # API endpoints
        if self.api_endpoints:
            parts.append("## API Endpoints")
            for ep in self.api_endpoints:
                parts.append(f"- {ep.method} {ep.path} ({ep.file_path})")

        # Database schema
        if self.db_schema.tables:
            parts.append("\n## Database Tables")
            for table in self.db_schema.tables:
                cols = ", ".join(f"{c.name}:{c.data_type}" for c in table.columns)
                parts.append(f"- {table.name} [{cols}]")

        # React components
        if self.react_components:
            parts.append("\n## React Components")
            for comp in self.react_components:
                parts.append(f"- {comp}")

        # Dependencies
        if self.dependencies:
            parts.append("\n## Dependencies")
            for dep in self.dependencies:
                parts.append(f"- [{dep.layer}] {dep.name} {dep.version}")

        result = "\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n\n... (truncated)"
        return result
