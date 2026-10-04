import logging

logger = logging.getLogger(__name__)

class RateLimitException(Exception):
    def __init__(self, message="Rate limit exceeded", status_code=429):
        super().__init__(message)
        self.status_code = status_code

class FallbackProviderChain:
    def __init__(self, providers: list):
        self.providers = providers
        
    def execute(self, *args, **kwargs):
        for idx, provider in enumerate(self.providers):
            try:
                # Assuming provider has an execute or __call__ method
                if hasattr(provider, "execute"):
                    return provider.execute(*args, **kwargs)
                elif callable(provider):
                    return provider(*args, **kwargs)
            except Exception as e:
                # Check for 429 status code or RateLimitException
                if isinstance(e, RateLimitException) or (hasattr(e, "status_code") and e.status_code == 429) or (hasattr(e, "response") and hasattr(e.response, "status_code") and e.response.status_code == 429):
                    logger.warning(f"Provider at index {idx} hit rate limit. Falling back to next provider.")
                    continue
                else:
                    raise e
        raise Exception("All providers failed or hit rate limits.")
