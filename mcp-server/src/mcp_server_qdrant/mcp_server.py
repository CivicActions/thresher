import json
import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field
from qdrant_client import models

from mcp_server_qdrant.common.filters import make_indexes
from mcp_server_qdrant.common.func_tools import make_partial_function
from mcp_server_qdrant.common.wrap_filters import wrap_filters
from mcp_server_qdrant.embeddings.base import EmbeddingProvider
from mcp_server_qdrant.embeddings.factory import (
    create_collection_providers,
    create_embedding_provider,
)
from mcp_server_qdrant.qdrant import ArbitraryFilter, Entry, QdrantConnector
from mcp_server_qdrant.settings import EmbeddingProviderSettings, QdrantSettings, ToolSettings

logger = logging.getLogger(__name__)


# FastMCP is an alternative interface for declaring the capabilities
# of the server. Its API is based on FastAPI.
class QdrantMCPServer(FastMCP):
    """
    A MCP server for Qdrant.
    """

    def __init__(
        self,
        tool_settings: ToolSettings,
        qdrant_settings: QdrantSettings,
        embedding_provider_settings: EmbeddingProviderSettings | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        name: str = "mcp-server-qdrant",
        instructions: str | None = None,
        **settings: Any,
    ):
        self.tool_settings = tool_settings
        self.qdrant_settings = qdrant_settings

        if embedding_provider_settings and embedding_provider:
            raise ValueError(
                "Cannot provide both embedding_provider_settings and embedding_provider"
            )

        if not embedding_provider_settings and not embedding_provider:
            raise ValueError(
                "Must provide either embedding_provider_settings or embedding_provider"
            )

        self.embedding_provider_settings: EmbeddingProviderSettings | None = None
        self.embedding_provider: EmbeddingProvider | None = None

        if embedding_provider_settings:
            self.embedding_provider_settings = embedding_provider_settings
            self.embedding_provider = create_embedding_provider(embedding_provider_settings)
        else:
            self.embedding_provider_settings = None
            self.embedding_provider = embedding_provider

        assert self.embedding_provider is not None, "Embedding provider is required"

        # When multi-collection config is present, create per-collection providers
        collection_providers = {}
        if qdrant_settings.collections:
            collection_providers = create_collection_providers(qdrant_settings.collections)

        self.qdrant_connector = QdrantConnector(
            qdrant_settings.location,
            qdrant_settings.api_key,
            qdrant_settings.collection_name or qdrant_settings.default_collection or None,
            self.embedding_provider,
            qdrant_settings.local_path,
            make_indexes(qdrant_settings.filterable_fields_dict()),
            embedding_providers=collection_providers if collection_providers else None,
        )

        super().__init__(name=name, instructions=instructions, **settings)

        self.setup_tools()

    def format_entry(self, entry: Entry) -> str:
        """
        Feel free to override this method in your subclass to customize the format of the entry.
        """
        entry_metadata = json.dumps(entry.metadata) if entry.metadata else ""
        return (
            f"<entry><content>{entry.content}</content>"
            f"<metadata>{entry_metadata}</metadata></entry>"
        )

    def setup_tools(self):
        """
        Register the tools in the server.
        """

        async def find(
            ctx: Context,
            query: Annotated[str, Field(description="What to search for")],
            collection_name: Annotated[str, Field(description="The collection to search in")],
            num_results: Annotated[
                int | None,
                Field(
                    description=(
                        "Number of results to return. Defaults to QDRANT_SEARCH_LIMIT."
                        " Capped at QDRANT_SEARCH_LIMIT_MAX when set."
                    ),
                    default=None,
                ),
            ] = None,
            offset: Annotated[
                int | None,
                Field(
                    description=(
                        "Number of top-ranked results to skip before returning entries. "
                        "Use together with num_results for pagination."
                    ),
                    default=None,
                ),
            ] = None,
            source_path: Annotated[
                str | None,
                Field(
                    description=(
                        "Filter by the top-level 'source' payload field (exact match)."
                        " Use a thresher source path, e.g. 'gs://bucket/file.pdf'."
                    ),
                    default=None,
                ),
            ] = None,
            query_filter: ArbitraryFilter | None = None,
        ) -> list[str] | None:
            """
            Find memories in Qdrant.
            :param ctx: The context for the request.
            :param query: The query to use for the search.
            :param collection_name: The collection to search in. If not provided, the
                                     default collection is used.
            :param num_results: Maximum number of results to return. Capped by server max if
                                configured.
            :param offset: Number of results to skip for pagination.
            :param source_path: Filter to entries matching this source path exactly.
            :param query_filter: Additional arbitrary filter to apply to the query.
            :return: A list of entries found or None.
            """

            # Log query_filter
            await ctx.debug(f"Query filter: {query_filter}")

            base_filter = models.Filter(**query_filter) if query_filter else None

            # Merge source_path into the filter when provided
            if source_path is not None:
                source_condition = models.FieldCondition(
                    key="source",
                    match=models.MatchValue(value=source_path),
                )
                if base_filter is not None:
                    existing_must = list(base_filter.must or [])
                    base_filter = models.Filter(
                        must=existing_must + [source_condition],
                        must_not=base_filter.must_not,
                        should=base_filter.should,
                    )
                else:
                    base_filter = models.Filter(must=[source_condition])

            # Resolve effective limit: apply optional max cap
            default_limit = self.qdrant_settings.search_limit
            max_limit = self.qdrant_settings.search_limit_max
            if num_results is not None:
                effective_limit = (
                    min(num_results, max_limit) if max_limit is not None else num_results
                )
            else:
                effective_limit = default_limit

            await ctx.debug(f"Finding results for query {query}")

            entries = await self.qdrant_connector.search(
                query,
                collection_name=collection_name,
                limit=effective_limit,
                offset=offset,
                query_filter=base_filter,
            )
            if not entries:
                return None
            content = [
                f"Results for the query '{query}'",
            ]
            for entry in entries:
                content.append(self.format_entry(entry))
            return content

        find_foo = find

        filterable_conditions = self.qdrant_settings.filterable_fields_dict_with_conditions()

        if len(filterable_conditions) > 0:
            find_foo = wrap_filters(find_foo, filterable_conditions)
        elif not self.qdrant_settings.allow_arbitrary_filter:
            find_foo = make_partial_function(find_foo, {"query_filter": None})

        if self.qdrant_settings.collection_name:
            find_foo = make_partial_function(
                find_foo, {"collection_name": self.qdrant_settings.collection_name}
            )

        self.tool(
            find_foo,
            name="qdrant-find",
            description=self.tool_settings.tool_find_description,
        )
