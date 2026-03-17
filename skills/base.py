"""
Base skill class for all Claude-powered research skills.

Each skill follows a consistent pattern:
  1. Define system_prompt and build user_prompt from inputs
  2. Call Claude via the wrapper
  3. Parse and save the results
"""
import time
from abc import ABC, abstractmethod
from claude_wrapper import get_claude, ClaudeWrapper
from utils import logger


class BaseSkill(ABC):
    """Abstract base class for all research skills."""

    name: str = "base"
    description: str = "Base skill"

    def __init__(self, claude: ClaudeWrapper = None):
        self.claude = claude or get_claude()

    @abstractmethod
    def execute(self, **kwargs) -> dict:
        """
        Execute the skill with the given parameters.

        Returns:
            A dict with at least:
              - "success": bool
              - "result": the main output (str, dict, list, etc.)
              - any other skill-specific keys
        """
        pass

    def run(self, **kwargs) -> dict:
        """
        Run the skill with logging, timing, and error handling.

        This is the public entry point. It wraps execute() with
        consistent logging and error handling.
        """
        logger.info(f"{'='*60}")
        logger.info(f"SKILL: {self.name}")
        logger.info(f"Description: {self.description}")
        logger.info(f"Params: {kwargs}")
        logger.info(f"{'='*60}")

        start = time.time()
        try:
            result = self.execute(**kwargs)
            elapsed = time.time() - start
            logger.info(f"SKILL {self.name} completed in {elapsed:.1f}s")
            result["elapsed_seconds"] = round(elapsed, 1)
            return result
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"SKILL {self.name} failed after {elapsed:.1f}s: {e}")
            return {
                "success": False,
                "error": str(e),
                "elapsed_seconds": round(elapsed, 1),
            }