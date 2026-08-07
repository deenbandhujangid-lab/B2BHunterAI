from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import aiosmtplib
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from app.core.config import settings

logger = logging.getLogger(__name__)

PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "protonmail.com", "rediffmail.com",
    "ymail.com", "aol.com",
}

PERMUTATION_TEMPLATES = [
    "{first}.{last}@{domain}",
    "{first}{last}@{domain}",
    "{first}@{domain}",
    "{f}{last}@{domain}",
    "{first}_{last}@{domain}",
    "{last}.{first}@{domain}",
    "{first}-{last}@{domain}",
    "{last}{f}@{domain}",
    "{f}.{last}@{domain}",
]


@dataclass
class VerifyResult:
    email: str
    status: str  # VALID, RISKY_CATCHALL, INVALID, UNVERIFIED
    detail: str
    mx_host: str | None = None


def generate_email_permutations(
    first_name: str, last_name: str, domain: str
) -> list[str]:
    """Generate standard corporate email patterns."""
    first = re.sub(r"[^a-z]", "", first_name.lower())
    last = re.sub(r"[^a-z]", "", last_name.lower()) if last_name else ""
    f = first[0] if first else ""
    domain = domain.lower().replace("www.", "")

    emails: list[str] = []
    for tpl in PERMUTATION_TEMPLATES:
        try:
            email = tpl.format(first=first, last=last, f=f, domain=domain)
            if "@" in email and "." in email.split("@")[1]:
                emails.append(email)
        except (KeyError, IndexError):
            continue
    return list(dict.fromkeys(emails))


class EmailVerifier:
    """100% free 4-tier email verification."""

    async def verify(
        self, email: str, domain: str | None = None, *, quick: bool = False
    ) -> VerifyResult:
        email = email.strip().lower()

        # Tier 1: Syntax + block personal domains
        try:
            validated = validate_email(email, check_deliverability=False)
            email = validated.normalized
        except EmailNotValidError as e:
            return VerifyResult(email, "INVALID", f"Syntax: {e}")

        email_domain = email.split("@")[1]
        if email_domain in PERSONAL_DOMAINS:
            return VerifyResult(email, "INVALID", "Personal email domain blocked")

        # Tier 2: MX lookup (short). On quick path, still save without MX so leads keep flowing.
        mx_host = await self._mx_lookup(email_domain)
        if not mx_host:
            if quick:
                return VerifyResult(
                    email, "UNVERIFIED", "No MX (saved for outreach)", None
                )
            return VerifyResult(email, "INVALID", "No MX records")

        # Live-save path: skip slow SMTP so DB updates immediately
        if quick:
            return VerifyResult(
                email, "UNVERIFIED", "MX ok (saved live; SMTP skipped)", mx_host
            )

        # Tier 3 + 4: SMTP probe + catch-all check
        return await self._smtp_verify(email, email_domain, mx_host)

    async def _mx_lookup(self, domain: str) -> str | None:
        loop = asyncio.get_event_loop()
        try:
            answers = await loop.run_in_executor(
                None,
                lambda: dns.resolver.resolve(
                    domain, "MX", lifetime=settings.MX_LOOKUP_TIMEOUT
                ),
            )
            mx_list = sorted(
                [(r.preference, str(r.exchange).rstrip(".")) for r in answers],
                key=lambda x: x[0],
            )
            return mx_list[0][1] if mx_list else None
        except Exception as e:
            logger.debug("MX lookup failed %s: %s", domain, e)
            return None

    async def _smtp_verify(
        self, email: str, domain: str, mx_host: str
    ) -> VerifyResult:
        """Tier 3 SMTP RCPT TO + Tier 4 catch-all probe."""
        try:
            smtp = aiosmtplib.SMTP(
                hostname=mx_host,
                port=25,
                timeout=settings.SMTP_VERIFY_TIMEOUT,
                use_tls=False,
            )
            await smtp.connect()
            await smtp.ehlo()
            await smtp.mail(settings.SMTP_FROM_EMAIL)

            # Tier 4: Catch-all check with fake mailbox
            fake = f"probe_xyz123@{domain}"
            fake_code, fake_msg = await smtp.rcpt(fake)
            if fake_code == 250:
                await smtp.quit()
                return VerifyResult(
                    email,
                    "RISKY_CATCHALL",
                    "Domain accepts all addresses (catch-all)",
                    mx_host,
                )

            # Tier 3: Real mailbox check
            code, message = await smtp.rcpt(email)
            await smtp.quit()
            msg = message.decode() if isinstance(message, bytes) else str(message)

            if code == 250:
                return VerifyResult(email, "VALID", f"SMTP 250 OK: {msg}", mx_host)
            if code in (550, 551, 553):
                return VerifyResult(email, "INVALID", f"SMTP rejected: {msg}", mx_host)
            return VerifyResult(email, "UNVERIFIED", f"SMTP code {code}: {msg}", mx_host)

        except asyncio.TimeoutError:
            return VerifyResult(email, "UNVERIFIED", "Port 25 blocked or timed out", mx_host)
        except aiosmtplib.SMTPRecipientsRefused:
            return VerifyResult(email, "INVALID", "Recipient refused", mx_host)
        except Exception as e:
            logger.debug("SMTP error for %s: %s", email, e)
            return VerifyResult(
                email, "UNVERIFIED", f"Port 25 unavailable: {e}", mx_host
            )

    def extract_emails(self, text: str) -> list[str]:
        if not text:
            return []
        # Decode common HTML/obfuscation before regex
        text = (
            text.replace("&#64;", "@")
            .replace("&#x40;", "@")
            .replace("&amp;", "&")
            .replace("[at]", "@")
            .replace("(at)", "@")
            .replace("[dot]", ".")
            .replace("(dot)", ".")
        )
        # "info at company.com" / "hello [at] company.com" (spaces around @)
        text = re.sub(
            r"([a-zA-Z0-9._%+\-]+)\s*(?:@|\[at\]|\(at\)|\bat\b|\bAT\b)\s*([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
            r"\1@\2",
            text,
        )
        text = re.sub(
            r"([a-zA-Z0-9._%+\-]+)\s*(?:\.|\[dot\]|\(dot\)|\bdot\b|\bDOT\b)\s*([a-zA-Z]{2,})\b",
            r"\1.\2",
            text,
        )
        pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
        found = re.findall(pattern, text)
        skip_ext = {".png", ".jpg", ".gif", ".svg", ".webp", ".css", ".js"}
        cleaned = []
        for e in found:
            el = e.lower().strip(".,;:()<>[]{}\"'")
            if any(el.endswith(x) for x in skip_ext):
                continue
            if "@" not in el:
                continue
            dom = el.split("@")[1]
            if dom in PERSONAL_DOMAINS:
                continue
            if len(el) > 80:
                continue
            cleaned.append(el)
        return list(dict.fromkeys(cleaned))
