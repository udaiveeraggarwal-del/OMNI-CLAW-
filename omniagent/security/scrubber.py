"""
Privacy & Fingerprint Scrubber for OmniAgent Network and Browser Operations.

Provides:
- HTTP request header sanitization (purging tracking headers, IP leak headers, Client Hints).
- User-Agent and locale normalization to Tor Browser ESR standards.
- BrowserShield providing Playwright/Chromium anti-fingerprinting flags and stealth scripts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set


TOR_BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
CHROME_NORMALIZED_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Tracking, identity, and proxy leakage headers to purge
DEFAULT_TRACKING_HEADERS: Set[str] = {
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "client-ip",
    "true-client-ip",
    "cf-connecting-ip",
    "via",
    "forwarded",
    "x-request-id",
    "x-correlation-id",
    "x-client-data",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-arch",
    "sec-ch-ua-model",
    "sec-ch-ua-platform-version",
    "sec-ch-ua-bitness",
    "sec-ch-ua-full-version-list",
}


class FingerprintScrubber:
    """
    Sanitizes HTTP headers to eliminate identity and location leaks.
    """

    def __init__(
        self,
        user_agent: str = TOR_BROWSER_USER_AGENT,
        accept_language: str = "en-US,en;q=0.5",
        custom_tracking_headers: Optional[Set[str]] = None,
    ):
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.tracking_headers = set(DEFAULT_TRACKING_HEADERS)
        if custom_tracking_headers:
            self.tracking_headers.update(h.lower() for h in custom_tracking_headers)

    def is_tracking_header(self, header_name: str) -> bool:
        """Check if header matches known tracking patterns (case-insensitive)."""
        lower = header_name.strip().lower()
        if lower in self.tracking_headers:
            return True
        if lower.startswith("sec-ch-ua") or lower.startswith("x-forwarded-"):
            return True
        return False

    def scrub_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """
        Purges tracking headers and applies standardized identity headers.
        Preserves essential headers (Host, Authorization, Content-Type, etc.).
        """
        cleaned: Dict[str, str] = {}
        for k, v in headers.items():
            if not self.is_tracking_header(k):
                cleaned[k] = v

        cleaned["User-Agent"] = self.user_agent
        cleaned["Accept-Language"] = self.accept_language
        return cleaned

    @classmethod
    def scrub(cls, headers: Dict[str, str]) -> Dict[str, str]:
        """Convenience class method using default Tor ESR profile."""
        return cls().scrub_headers(headers)


class BrowserShield:
    """
    Constructs launch arguments and stealth scripts for headless browser automation (Playwright/Chromium).
    Mitigates WebRTC IP leaks, canvas fingerprinting, and timezone correlation.
    """

    DEFAULT_FLAGS: List[str] = [
        "--disable-webrtc",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--timezone=UTC",
        "--lang=en-US",
        "--deny-permission-prompts",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--password-store=basic",
        "--use-mock-keychain",
    ]

    @classmethod
    def get_launch_args(cls, proxy_url: Optional[str] = None) -> List[str]:
        """Generate Chromium/Edge command line launch flags."""
        args = list(cls.DEFAULT_FLAGS)
        if proxy_url:
            args.append(f"--proxy-server={proxy_url}")
        args.append(f"--user-agent={TOR_BROWSER_USER_AGENT}")
        return args

    @classmethod
    def get_stealth_scripts(cls) -> List[str]:
        """JavaScript snippets to inject into new pages to mask fingerprinting APIs."""
        return [
            # Mask navigator.webdriver
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """,
            # Canvas noise injection
            """
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                const ctx = this.getContext('2d');
                if (ctx) {
                    const imgData = ctx.getImageData(0, 0, Math.min(this.width, 16), Math.min(this.height, 16));
                    // Imperceptible noise
                    for (let i = 0; i < imgData.data.length; i += 4) {
                        imgData.data[i] = imgData.data[i] ^ 1;
                    }
                    ctx.putImageData(imgData, 0, 0);
                }
                return originalToDataURL.apply(this, arguments);
            };
            """,
            # WebRTC leak protection
            """
            if (window.RTCPeerConnection) {
                window.RTCPeerConnection = undefined;
            }
            if (window.webkitRTCPeerConnection) {
                window.webkitRTCPeerConnection = undefined;
            }
            """,
        ]
