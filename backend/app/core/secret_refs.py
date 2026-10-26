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
