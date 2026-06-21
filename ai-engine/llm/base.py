"""
Abstract base class for LLM providers.
Each provider (OpenAI, Anthropic) implements this interface.
"""

from abc import ABC, abstractmethod
from models import QueryPlan, PlanRequest


class BaseLLMProvider(ABC):
    """
    Abstract LLM provider.

    Architecture boundary:
      Providers ONLY produce QueryPlan objects.
      They have no access to databases, Trino, or any infrastructure.
      They are pure text-in / structured-JSON-out components.
    """

    @abstractmethod
    async def generate_plan(
        self, system_prompt: str, user_prompt: str, question: str
    ) -> QueryPlan:
        """
        Generate a QueryPlan from the given prompts.

        Args:
            system_prompt: The schema context and instructions
            user_prompt: The user's question and formatting instructions
            question: The original question (for plan metadata)

        Returns:
            A validated QueryPlan object

        Raises:
            ValueError: If the LLM output cannot be parsed or fails validation
        """
        ...
