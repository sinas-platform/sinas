"""Secret references: how a resource names the Secret that holds a credential.

One rule everywhere a resource points at a Secret (connector and MCP server
`auth.secret`, package repository `auth.secret`, identity provider
`client_secret`): the reference is written either as the bare name, `NAME`,
or wrapped in braces, `{{ NAME }}` (whitespace inside the braces optional),
and both resolve to the Secret named `NAME`.

The braced form is what the official packages write, and what a package
`type: secret` variable substitutes to (`${{ vars.NAME }}` -> `{{NAME}}`).
Specs store the bare name; resolvers unwrap again so rows stored before that
normalisation keep working.
"""

import re
from typing import Optional

_BRACED = re.compile(r"^\{\{\s*([^{}\s]+)\s*\}\}$")


def secret_ref_name(ref: Optional[str]) -> Optional[str]:
    """The Secret name a reference points at: `NAME` and `{{ NAME }}` -> `NAME`.

    Anything else (None, an empty string) is returned unchanged.
    """
    if not isinstance(ref, str):
        return ref
    stripped = ref.strip()
    match = _BRACED.match(stripped)
    return match.group(1) if match else stripped


# What a Secret may be called (the REST rule; config and packages enforce it
# on create too), so `{{ NAME }}` can always reference it.
SECRET_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"

# Names shown in messages are at most this long.
_MAX_SHOWN_NAME = 100
# Prefixes of well-known credential formats that are also valid identifiers.
_CREDENTIAL_PREFIXES = (
    "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "sk_", "rk_", "pk_", "xox",
    "AKIA", "ASIA", "AIza", "eyJ", "shpat_", "shpss_", "shpca_", "shppa_", "npm_", "hf_",
    "re_", "pat_", "sq0", "whsec_", "glpat_",
)
# A long run of letters AND digits (or a very long run of anything): a
# random token, not a name.
_RANDOM_RUN = re.compile(r"[A-Za-z0-9]{20,}")
_VERY_LONG_RUN = re.compile(r"[A-Za-z0-9]{32,}")


def looks_like_secret_name(ref: Optional[str]) -> bool:
    """Whether a reference may be shown in a message.

    Someone may paste the credential itself where its name belongs
    (`secret: ghp_...`); then the "name" is the value, and must not travel
    to a chat, an LLM or a pipeline record. Shape-based, so it errs on the
    side of hiding: only what a new Secret may be called (SECRET_NAME_PATTERN,
    so no UUIDs, `pat-na1-...` or `user:pass`), minus known token prefixes,
    long random-looking runs, and password-like words (`Summer2024`,
    `realToken123`: mixed case with digits in one segment).
    """
    name = secret_ref_name(ref)
    if (
        not isinstance(name, str)
        or len(name) > _MAX_SHOWN_NAME
        or not re.match(SECRET_NAME_PATTERN, name)
    ):
        return False
    if name.startswith(_CREDENTIAL_PREFIXES) or _VERY_LONG_RUN.search(name):
        return False
    for run in _RANDOM_RUN.findall(name):
        if any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
            return False
    for segment in name.split("_"):
        if (
            any(c.isdigit() for c in segment)
            and any(c.islower() for c in segment)
            and any(c.isupper() for c in segment)
        ):
            return False
    return True


def describe_secret_ref(ref: Optional[str]) -> str:
    """`secret 'NAME'` for a name-like reference, else a neutral phrase."""
    if looks_like_secret_name(ref):
        return f"secret '{secret_ref_name(ref)}'"
    return "the configured secret reference"

